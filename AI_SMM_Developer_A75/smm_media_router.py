"""SMM media execution routing with no speculative provider capabilities.

This module makes a PLAN, not an API call. Provider health/capability must be
explicitly verified by an adapter; an unconnected web demo is never a free
API endpoint. All paid runs require a separate explicit approval after quote.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

IMAGE_STYLE = "image_style"
IMAGE_STYLE_REFERENCE = "image_style_reference"
IMAGE_REMOVE_BACKGROUND = "image_remove_background"
IMAGE_REPLACE_BACKGROUND = "image_replace_background"
IMAGE_ADD_OBJECT = "image_add_object"
IMAGE_REMOVE_OBJECT = "image_remove_object"
IMAGE_RESIZE = "image_resize"
VIDEO_CUT = "video_cut"
VIDEO_MONTAGE = "video_montage"
VIDEO_STYLE = "video_style"
VIDEO_STYLE_REFERENCE = "video_style_reference"
VIDEO_FROM_IMAGES = "video_from_images"
VIDEO_FORMAT = "video_format"
TASKS = frozenset((
    IMAGE_STYLE,IMAGE_STYLE_REFERENCE,IMAGE_REMOVE_BACKGROUND,
    IMAGE_REPLACE_BACKGROUND,IMAGE_ADD_OBJECT,IMAGE_REMOVE_OBJECT,IMAGE_RESIZE,
    VIDEO_CUT,VIDEO_MONTAGE,VIDEO_STYLE,VIDEO_STYLE_REFERENCE,
    VIDEO_FROM_IMAGES,VIDEO_FORMAT,
))
REFERENCE_TASKS = frozenset((IMAGE_STYLE_REFERENCE, VIDEO_STYLE_REFERENCE))
SOURCE_TASKS = TASKS - frozenset((IMAGE_STYLE,))
TIERS = ("local", "free_cloud", "paid_cloud")


@dataclass(frozen=True)
class MediaRequest:
    task: str
    source_count: int
    prompt: str = ""
    has_style_reference: bool = False

    def __post_init__(self):
        if self.task not in TASKS:
            raise ValueError("unsupported SMM media task")
        if type(self.source_count) is not int or not 0 <= self.source_count <= 30:
            raise ValueError("invalid media source count")
        if self.task in SOURCE_TASKS and self.source_count == 0:
            raise ValueError("SMM operation requires source media")
        if self.task in REFERENCE_TASKS and not self.has_style_reference:
            raise ValueError("SMM style-by-reference requires explicit reference")
        if type(self.has_style_reference) is not bool:
            raise ValueError("invalid style reference flag")
        if not isinstance(self.prompt,str) or len(self.prompt)>20000:
            raise ValueError("invalid SMM media instructions")


@dataclass(frozen=True)
class Executor:
    name: str
    tier: str
    supports: FrozenSet[str]
    verified_ready: bool = False
    verified_quote_cents: int | None = None

    def __post_init__(self):
        if not isinstance(self.name,str) or not self.name.strip():
            raise ValueError("executor name required")
        if self.tier not in TIERS:
            raise ValueError("invalid execution tier")
        if not isinstance(self.supports,frozenset) or not self.supports <= TASKS:
            raise ValueError("unverified executor capability")
        if type(self.verified_ready) is not bool:
            raise ValueError("invalid executor readiness")
        if self.verified_quote_cents is not None:
            if (type(self.verified_quote_cents) is not int or
                    not 1<=self.verified_quote_cents<=10_000_000):
                raise ValueError("invalid provider price quote")
            if self.tier!="paid_cloud":
                raise ValueError("only paid providers carry a cost quote")


@dataclass(frozen=True)
class Decision:
    state: str
    executor: str | None
    tier: str | None
    reason: str


def select_executor(request: MediaRequest, executors, *, paid_approved=False):
    """Route local -> verified free -> verified paid without external effects.

    Even an approved paid job never auto-starts here; the paid call remains
    behind durable budget reservation and a separate explicit consent gate.
    """
    if not isinstance(request,MediaRequest):
        raise TypeError("validated MediaRequest required")
    if type(paid_approved) is not bool:
        raise ValueError("paid approval must be an exact boolean")
    candidates=list(executors)
    if any(not isinstance(x,Executor) for x in candidates):
        raise TypeError("all media executors must be declared")
    names=[x.name for x in candidates]
    if len(names)!=len(set(names)):
        raise ValueError("duplicate media executor identity")
    compatible=[x for x in candidates if x.verified_ready and request.task in x.supports]
    for tier in TIERS:
        for executor in compatible:
            if executor.tier!=tier:
                continue
            if tier=="paid_cloud":
                if executor.verified_quote_cents is None:
                    continue
                if not paid_approved:
                    return Decision("needs_paid_approval",executor.name,tier,
                                    "verified quote and explicit user approval required")
                return Decision("ready_for_paid_boundary",executor.name,tier,
                                "budget allocation and final consent still required")
            return Decision("ready",executor.name,tier,"verified capability available")
    return Decision("unavailable",None,None,"no verified compatible executor")


def default_executors(*, ffmpeg_ready=False,pillow_ready=False):
    """Never claim that ffmpeg provides AI editing, background removal, etc."""
    if type(ffmpeg_ready) is not bool or type(pillow_ready) is not bool:
        raise ValueError("ffmpeg readiness must be verified")
    return (Executor("local-ffmpeg","local",frozenset((
        VIDEO_CUT,VIDEO_MONTAGE,VIDEO_FORMAT,VIDEO_FROM_IMAGES,
    )),verified_ready=ffmpeg_ready),
        Executor('local-pillow','local',frozenset((IMAGE_RESIZE,)),verified_ready=pillow_ready))
