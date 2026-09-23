"""TerminologyMemory tests — 'YG' stays 'YG', never 'Why Gee'."""
from transcriptor.translation.terminology import TerminologyMemory


def test_register_and_overrides():
    mem = TerminologyMemory()
    mem.register("YG", "YG", context="group name")
    assert mem.overrides() == {"YG": "YG"}


def test_apply_enforces_variants():
    mem = TerminologyMemory()
    mem.register("YG", "YG")
    # MT engine rendered it as dotted or spaced variants — all must collapse.
    assert mem.apply("Y.G. is the company behind the group") == "YG is the company behind the group"
    assert mem.apply("Y G Entertainment held auditions") == "YG Entertainment held auditions"


def test_apply_is_conservative():
    mem = TerminologyMemory()
    mem.register("BTS", "BTS")
    text = "butter is a song by bts"
    assert mem.apply(text) == "butter is a song by BTS"


def test_learn_from_repetition():
    texts = ["YG family concert", "YG audition", "YG treasure box", "other words"]
    mem = TerminologyMemory()
    learned = mem.learn_from_repetition(texts, min_occurrences=3)
    assert any(e.source_term == "YG" for e in learned)
    assert mem.overrides().get("YG") == "YG"


def test_consistency_finding_on_inconsistent_variant():
    mem = TerminologyMemory()
    mem.register("YG", "YG")
    findings = mem.consistency_findings([
        ("YG announced", "YG announced"),          # fine
        ("YG again", "Y.G. again"),                # mechanical drift — detected
        # Free-form mistranslations (e.g. "Why Gee") are NOT mechanically
        # derivable — those are caught by the LLM reviewer pass (qa.LLMReviewer).
    ])
    assert any(f.code == "TERMINOLOGY_INCONSISTENT" for f in findings)


def test_user_override_wins():
    mem = TerminologyMemory()
    mem.register("仁", "Jin")
    entry = mem.override("仁", "Ren")
    assert entry.user_override
    assert mem.overrides()["仁"] == "Ren"
