import uuid
from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://scout:scout@localhost:5432/scout"
    sam_gov_api_key: str = ""
    scout_session_id: str = ""

    # Email outreach (ARCHITECTURE.md §3.6/§3.7). smtp_password is a Gmail App Password,
    # not the account password -- Gmail requires this for SMTP when 2FA is on. Generate
    # one at https://myaccount.google.com/apppasswords
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""

    # First day of the warmup ramp -- defaults to today the first time this is read, but
    # should be pinned once real sending starts (see cli/warmup below) so the ramp doesn't
    # silently reset. Format: YYYY-MM-DD.
    email_warmup_start_date: str = ""

    # Reply watching (ARCHITECTURE.md §3.8). Same Gmail account as SMTP -- a Gmail App
    # Password is valid for IMAP too, no separate credential needed.
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"

    def session_id(self) -> str:
        return self.scout_session_id or str(uuid.uuid4())

    def warmup_start(self) -> date:
        if self.email_warmup_start_date:
            return date.fromisoformat(self.email_warmup_start_date)
        return date.today()


settings = Settings()
