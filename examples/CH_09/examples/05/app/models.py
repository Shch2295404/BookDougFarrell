from contextlib import contextmanager
from enum import Flag, auto
from flask import current_app
from flask_bcrypt import (
    generate_password_hash,
    check_password_hash
)
from . import db
from flask_login import UserMixin
from uuid import uuid4
from datetime import datetime, timezone
from itsdangerous import (
    URLSafeTimedSerializer,
    SignatureExpired,
    BadSignature
)
from time import time
import jwt


@contextmanager
def db_session_manager(session_close=True):
    """Creates a context manager to use to interact
    with the database session and assure closing
    the session at the end of the scope

    Yields:
        Session: The database session object to use
    """
    try:
        yield db.session
    except Exception:
        db.session.rollback()
        raise
    finally:
        if session_close:
            db.session.close()


def get_uuid():
    """Generate a shortened UUID4 value to use
    as the primary key for database records

    Returns:
        string: A shortened (no '-' characters) UUID4 value
    """
    return uuid4().hex


class User(UserMixin, db.Model):
    """The User class to structure what
    a user looks like for the MyBlog application. This
    capitalizes on the flask_login UserMixin class for
    some default methods. The UserMixin class will be
    used more when users are persisted to a database.
    """
    __tablename__ = "user"
    user_uid = db.Column(db.String, primary_key=True, default=get_uuid)
    role_uid = db.Column(db.String, db.ForeignKey("role.role_uid"), index=True, nullable=False)
    first_name = db.Column(db.String, nullable=False)
    last_name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, nullable=False, unique=True, index=True)
    hashed_password = db.Column("password", db.String, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    confirmed = db.Column(db.Boolean, default=False)
    created = db.Column(db.DateTime, nullable=False, default=datetime.now(tz=timezone.utc))
    updated = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.now(tz=timezone.utc),
        onupdate=datetime.now(tz=timezone.utc)
    )

    def get_id(self):
        return self.user_uid

    @property
    def password(self):
        raise AttributeError("user password can't be read")

    @password.setter
    def password(self, password):
        self.hashed_password = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.hashed_password, password)

    def confirmation_token(self):
        serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
        return serializer.dumps({"confirm": self.user_uid})

    def confirm_token(self, token):
        serializer = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
        with db_session_manager() as db_session:
            confirmation_link_timeout = current_app.config.get("CONFIRMATION_LINK_TIMEOUT")
            timeout = confirmation_link_timeout * 60 * 1000
            try:
                data = serializer.loads(token, max_age=timeout)
                if data.get("confirm") != self.user_uid:
                    return False
                self.confirmed = True
                db_session.add(self)
                return True
            except (SignatureExpired, BadSignature):
                return False

    def get_reset_token(self, timeout):
        timeout *= 60
        return jwt.encode(
            {
                "reset_password": self.user_uid,
                "exp": time() + timeout,
            },
            current_app.config["SECRET_KEY"],
            algorithm="HS256"
        )

    @staticmethod
    def verify_reset_token(token):
        user_uid = jwt.decode(
            token,
            current_app.config["SECRET_KEY"],
            algorithms=["HS256"]
        )["reset_password"]
        return user_uid

    def __repr__(self):
        return f"""
        user_uid: {self.user_uid}
        name: {self.first_name} {self.last_name}
        email: {self.email}
        confirmed: {self.confirmed}
        active: {'True' if self.active else 'False'}
            role_uid: {self.role.role_uid}
            name: {self.role.name}
            description: {self.role.description}
            permissions: {self.role.permissions}
        """


class Role(db.Model):
    """The Role class which is essentially a lookup table
    used to contain the roles supported by the MyBlog
    application
    """
    class Permissions(Flag):
        """This internally defined class creates the
        permissions bitmasks. It's internal here
        just to contain it within the scope of the
        Role class

        Args:
            Flag (enum.Flag): The bitmask value of a permissions
        """
        REGISTERED = auto()
        EDITOR = auto()
        ADMINISTRATOR = auto()

    __tablename__ = "role"
    role_uid = db.Column(db.String, primary_key=True, default=get_uuid)
    name = db.Column(db.String, nullable=False, unique=True)
    description = db.Column(db.String, nullable=False)
    raw_permissions = db.Column(db.Integer)
    users = db.relationship("User", backref=db.backref("role", lazy="joined"))
    active = db.Column(db.Boolean, nullable=False, default=True)
    created = db.Column(db.DateTime, nullable=False, default=datetime.now(tz=timezone.utc))
    updated = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.now(tz=timezone.utc),
        onupdate=datetime.now(tz=timezone.utc)
    )

    @property
    def permissions(self):
        return Role.Permissions(self.raw_permissions)

    @staticmethod
    def initialize_role_table():
        """This static method is used to initialize/update the role table
        based on the roles list defined below. This is useful as the role
        table is a read-only lookup table that needs data in it to
        start with.
        """
        roles = [
            {
                "name": "user",
                "description": "registered user permission",
                "raw_permissions": Role.Permissions.REGISTERED.value
            },
            {
                "name": "editor",
                "description": "user has ability to edit all content and comments",
                "raw_permissions": (Role.Permissions.REGISTERED | Role.Permissions.EDITOR).value
            },
            {
                "name": "admin",
                "description": "administrator user with access to all of the application",
                "raw_permissions": (
                    Role.Permissions.REGISTERED |
                    Role.Permissions.EDITOR | Role.Permissions.ADMINISTRATOR
                ).value
            }
        ]
        with db_session_manager() as db_session:
            for r in roles:
                role = db_session.query(Role).filter(Role.name == r.get("name")).one_or_none()

                # is there no existing role by a given name?
                if role is None:
                    role = Role(
                        name=r.get("name"),
                        description=r.get("description"),
                        raw_permissions=r.get("raw_permissions")
                    )
                # otherwise, need to update existing role permissions
                else:
                    role.description = r.get("description")
                    role.raw_permissions = r.get("raw_permissions")

                db_session.add(role)
            db_session.commit()

    def __repr__(self):
        return f"""
        role_uid: {self.role_uid}
        name: {self.name}, description: {self.description}
        permissions: {self.permissions}
        active: {'True' if self.active else 'False'}
        """
