"""Explicit generation roles with reserved independent reviewer capacity."""
from dataclasses import dataclass
import secrets
import re
from text_providers import is_ollama_cloud_tag, parse_cloud_model


def model_family(name):
    value = str(name).strip().casefold()
    provider, sep, model_id = value.partition('/')
    if sep and provider in ('openai','groq','openrouter','gemini'):
        # A different transport/provider is not an independent model voice.
        value=model_id.rsplit('/',1)[-1]
    value=value.split(':',1)[0]
    # A provider's size suffix is the same technical model family as an
    # Ollama size tag. Changing transport or size must not create a reader.
    value=re.sub(r'-\d+(?:\.\d+)?[bm](?:-(?:instruct|it|thinking|base))?$', '', value)
    # Qwen 2.5 Coder is a derivative of the same foundation, not an
    # independent family when the installed 14B base model writes/reviews.
    return 'qwen2.5' if value=='qwen2.5-coder' else value


MIN_REVIEWER_FAMILIES = 2


def authors_with_review_capacity(authors,reviewers,*,required=MIN_REVIEWER_FAMILIES):
    """Separate writer eligibility from independent reviewer availability."""
    families={model_family(name) for name in reviewers}
    accepted=[];deferred=[]
    for author in dict.fromkeys(authors):
        available=families-{model_family(author)}
        if len(available)>=required:accepted.append(author)
        else:deferred.append({'model':author,'available_reviewer_families':len(available),
            'required_reviewer_families':required,'reason':'insufficient_independent_review_capacity'})
    return accepted,deferred


@dataclass(frozen=True)
class RevisionRoles:
    initial_author: str
    revision_author: str
    reviewer_pool: tuple
    reason: str


def allocate_revision_roles(author, known_models, *, fixed_reviewers=None, chooser=None,
                            required_reviewers=4, previous_writers=()):
    """Use a different writer when four other reviewer families remain.

    Only already eligible models enter this function. No discovery, paid API
    opt-in or capability claim is implicit. An explicit reviewer assignment is
    preserved. The original writer is retained when independent capacity is
    insufficient; that fallback is reported, never hidden.
    """
    author = str(author).strip()
    if type(required_reviewers) is not int or required_reviewers<1:
        raise ValueError('Неверное число независимых рецензентов')
    models = list(dict.fromkeys([author, *(str(x).strip() for x in known_models if str(x).strip())]))
    author_family = model_family(author)
    writer_families={author_family,*(model_family(x) for x in previous_writers)}
    fixed = {model_family(x) for x in (fixed_reviewers or {}).values()}
    choices = []
    for candidate in models:
        family = model_family(candidate)
        if family == author_family or family in fixed:
            continue
        remaining = {model_family(m) for m in models} - writer_families - {family}
        if len(remaining) >= required_reviewers:
            choices.append(candidate)
    randomizer = chooser or secrets.SystemRandom()
    if choices:
        # Reserve an independent cloud source reviewer before assigning a
        # cloud writer. Consuming the only eligible cloud voice as reviser
        # must not silently downgrade all subsequent source decisions.
        cloud_families={model_family(m) for m in models if
            (is_ollama_cloud_tag(m) or parse_cloud_model(m) is not None)
            and model_family(m) not in writer_families}
        preserving=[m for m in choices if cloud_families-{model_family(m)}]
        if cloud_families and len(fixed)<required_reviewers:
            choices=preserving
    if choices:
        cloud = [m for m in choices if is_ollama_cloud_tag(m) or parse_cloud_model(m) is not None]
        reviser = randomizer.choice(cloud or choices)
        reason = 'distinct_writer_with_reserved_review_capacity'
    else:
        reviser = author
        reason = 'original_writer_retained_to_preserve_independent_review_capacity'
    excluded = writer_families | {model_family(reviser)}
    reviewers = tuple(m for m in models if model_family(m) not in excluded)
    return RevisionRoles(author, reviser, reviewers, reason)
