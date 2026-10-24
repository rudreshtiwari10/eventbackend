from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongodb_uri: str
    mongodb_db: str = "gayatrimaharas"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 12 * 60

    admin_username: str
    admin_password: str

    event_name: str = "GAYATRI MAHARAAS"
    event_date_label: str = "TBD"
    event_venue: str = "TBD"
    event_date: str  # YYYY-MM-DD, the only date volunteers may scan on

    cors_origins: str = "http://localhost:5173"

    class Config:
        env_file = ".env"

    @property
    def cors_origin_list(self):
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
