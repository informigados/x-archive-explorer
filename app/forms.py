from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional


class LoginForm(FlaskForm):
    email = StringField("E-mail ou usuário", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Senha", validators=[DataRequired(), Length(min=8)])
    submit = SubmitField("Entrar")


class ArchiveCreateForm(FlaskForm):
    name = StringField("Nome do arquivo", validators=[DataRequired(), Length(max=255)])
    description = TextAreaField("Descrição")
    submit = SubmitField("Criar")


class ArchiveUploadForm(FlaskForm):
    archive_file = FileField(
        "Arquivo ZIP/JSON",
        validators=[FileRequired(), FileAllowed(["zip", "json"], "Envie ZIP ou JSON.")],
    )
    import_mode = SelectField(
        "Modo de reimportação",
        choices=[
            ("merge", "Merge (mantém dados existentes e adiciona novos)"),
            ("overwrite", "Overwrite (substitui integralmente os posts do arquivo)"),
        ],
        default="merge",
        validators=[DataRequired()],
    )
    submit = SubmitField("Importar")


class ApiSyncForm(FlaskForm):
    username = StringField("Username", validators=[Optional(), Length(max=50)])
    user_id = StringField("User ID", validators=[Optional(), Length(max=100)])
    import_mode = SelectField(
        "Modo de sincronização",
        choices=[
            ("merge", "Merge (mantém dados existentes e adiciona novos)"),
            ("overwrite", "Overwrite (substitui integralmente os posts do arquivo)"),
        ],
        default="merge",
        validators=[DataRequired()],
    )
    include_replies = BooleanField("Incluir replies", default=True)
    incremental_sync = BooleanField("Sync incremental (usar since_id)", default=True)
    include_conversation_replies = BooleanField("Buscar replies por conversation_id", default=False)
    max_pages = IntegerField("Máximo de páginas", validators=[DataRequired(), NumberRange(min=1, max=50)], default=3)
    max_results = IntegerField(
        "Tweets por página",
        validators=[DataRequired(), NumberRange(min=5, max=100)],
        default=100,
    )
    conversation_max_pages = IntegerField(
        "Máximo de páginas por conversa",
        validators=[DataRequired(), NumberRange(min=1, max=20)],
        default=3,
    )
    conversation_reply_window_days = IntegerField(
        "Janela de replies da conversa (dias)",
        validators=[DataRequired(), NumberRange(min=0, max=365)],
        default=30,
    )
    submit = SubmitField("Sincronizar API")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        if not (self.username.data or self.user_id.data):
            msg = "Informe username ou user_id para sincronizar."
            self.username.errors.append(msg)
            return False
        return True


class UserCreateForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    role = SelectField(
        "Role",
        choices=[("viewer", "Viewer"), ("admin", "Administrator")],
        validators=[DataRequired()],
        default="viewer",
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    submit = SubmitField("Create user")


class UserEditForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=255)])
    role = SelectField(
        "Role",
        choices=[("viewer", "Viewer"), ("admin", "Administrator")],
        validators=[DataRequired()],
        default="viewer",
    )
    submit = SubmitField("Save changes")


class UserPasswordForm(FlaskForm):
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])
    password_confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password")],
    )
    submit = SubmitField("Update password")
