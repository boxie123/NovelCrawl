"""GUI 任务的请求契约，业务范围在入队时固化为稳定 ID。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[int, Field(gt=0, strict=True)]
Concurrency = Annotated[int, Field(ge=1, le=5, strict=True)]
Ids = Annotated[list[Positive], Field(min_length=1, max_length=100000)]


class RequestBase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=100)


class DownloadRequest(RequestBase):
    kind: Literal['download']
    url: str = Field(min_length=1, max_length=4096)
    start_position: Positive | None = None
    concurrency: Concurrency = 3


class ResumeRequest(RequestBase):
    kind: Literal['resume']
    source_id: Positive
    start_position: Positive | None = None
    concurrency: Concurrency = 3


class RefreshRequest(RequestBase):
    kind: Literal['refresh']
    source_id: Positive
    concurrency: Concurrency = 3
    all: bool = False
    chapters: str | None = Field(default=None, max_length=10000)
    chapter_ids: Ids | None = None

    @model_validator(mode='after')
    def selection(self):
        if sum((self.all, self.chapters is not None, self.chapter_ids is not None)) != 1:
            raise ValueError('全部刷新、章节范围、选中章节必须三选一')
        return self


class SourceExportRequest(RequestBase):
    kind: Literal['export_source']
    source_id: Positive


class MergeCreateRequest(RequestBase):
    kind: Literal['merge_create']
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    output_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode='after')
    def nonblank(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('合并版本名称不能为空')
        return self


class MergeAppendRequest(RequestBase):
    kind: Literal['merge_append']
    edition_id: Positive
    source_id: Positive
    chapters: str = Field(min_length=1, max_length=10000)


class MergeComposeRequest(RequestBase):
    kind: Literal['merge_compose']
    edition_id: Positive
    chapter_ids: Ids
    title: str | None = Field(default=None, max_length=300)


class MergedExportRequest(RequestBase):
    kind: Literal['export_merged']
    edition_id: Positive


TaskRequest = Annotated[
    DownloadRequest | ResumeRequest | RefreshRequest | SourceExportRequest |
    MergeCreateRequest | MergeAppendRequest | MergeComposeRequest | MergedExportRequest,
    Field(discriminator='kind'),
]
