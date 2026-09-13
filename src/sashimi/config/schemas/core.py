from pydantic import BaseModel, Field
from .thumbs import StaticThumbsConfig, AnimatedThumbsConfig


class GlobalConfig(BaseModel):
    verbose: int = Field(default=0, ge=0, description="Logging verbosity level")
    priority: str = Field(default="normal", description="Process priority (low, normal, high)")


class SashimiConfig(BaseModel):
    global_settings: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    thumbs: StaticThumbsConfig = Field(default_factory=StaticThumbsConfig)
    animated_thumbs: AnimatedThumbsConfig = Field(default_factory=AnimatedThumbsConfig)
