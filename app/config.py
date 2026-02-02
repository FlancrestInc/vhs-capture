import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    output_dir: str
    log_file: str
    host: str
    port: int
    auth_user: str | None
    auth_pass: str | None

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_user and self.auth_pass)


def load_config() -> AppConfig:
    output_dir = os.environ.get("VHS_OUTPUT_DIR", "/output")
    log_file = os.environ.get("VHS_LOG_FILE", os.path.join(output_dir, "vhs-ui.log"))
    host = os.environ.get("VHS_UI_HOST", "0.0.0.0")
    port = int(os.environ.get("VHS_UI_PORT", "8099"))
    auth_user = os.environ.get("VHS_UI_USER")
    auth_pass = os.environ.get("VHS_UI_PASS")
    return AppConfig(
        output_dir=output_dir,
        log_file=log_file,
        host=host,
        port=port,
        auth_user=auth_user,
        auth_pass=auth_pass,
    )
