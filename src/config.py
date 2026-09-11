import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class CerebrasSettings:
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("CEREBRAS_API_KEY", ""),
            base_url=os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
            model=os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"),
        )


@dataclass
class GitHubSettings:
    token: str = ""
    webhook_secret: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            token=os.getenv("GITHUB_TOKEN", ""),
            webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
        )


@dataclass
class SlackSettings:
    bot_token: str = ""
    signing_secret: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
        )


@dataclass
class Neo4jSettings:
    uri: str = ""
    username: str = ""
    password: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", "amaze_2026"),
        )


@dataclass
class SandboxSettings:
    timeout: int = 30
    memory_limit: str = "512m"
    cpu_quota: int = 50000
    network_disabled: bool = True
    node_image: str = "amaze-node:base"
    python_image: str = "amaze-python:base"

    @classmethod
    def from_env(cls):
        return cls(
            timeout=int(os.getenv("SANDBOX_TIMEOUT", "30")),
            memory_limit=os.getenv("SANDBOX_MEMORY_LIMIT", "512m"),
            cpu_quota=int(os.getenv("SANDBOX_CPU_QUOTA", "50000")),
        )


@dataclass
class AppSettings:
    port: int = 8000
    max_retries: int = 3
    risk_auto_merge_threshold: str = "LOW"

    @classmethod
    def from_env(cls):
        return cls(
            port=int(os.getenv("PORT", "8000")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            risk_auto_merge_threshold=os.getenv("RISK_AUTO_MERGE_THRESHOLD", "LOW"),
        )


@dataclass
class Settings:
    cerebras: CerebrasSettings = None
    github: GitHubSettings = None
    slack: SlackSettings = None
    neo4j: Neo4jSettings = None
    sandbox: SandboxSettings = None
    app: AppSettings = None

    def __post_init__(self):
        if self.cerebras is None:
            self.cerebras = CerebrasSettings.from_env()
        if self.github is None:
            self.github = GitHubSettings.from_env()
        if self.slack is None:
            self.slack = SlackSettings.from_env()
        if self.neo4j is None:
            self.neo4j = Neo4jSettings.from_env()
        if self.sandbox is None:
            self.sandbox = SandboxSettings.from_env()
        if self.app is None:
            self.app = AppSettings.from_env()


settings = Settings()
