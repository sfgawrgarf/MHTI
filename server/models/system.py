"""System configuration models."""

from pydantic import BaseModel, Field


class SystemConfig(BaseModel):
    """系统配置模型 - 统一性能配置"""

    scrape_threads: int = Field(default=4, ge=1, le=16, description="刮削任务线程数")
    task_timeout: int = Field(default=300, ge=10, le=1800, description="任务超时 (秒)")
    retry_count: int = Field(default=3, ge=0, le=10, description="失败重试次数")
    concurrent_downloads: int = Field(default=3, ge=1, le=10, description="并发下载数")
