import pytest

from sourcing1688 import messaging
from sourcing1688.lexicon import has_chinese
from sourcing1688.models import Offer, Supplier


@pytest.fixture
def offer():
    return Offer(offer_id="610001", title="260g重磅纯棉短袖T恤", price_cny=32.0, moq=50,
                 supplier=Supplier(seller_id="s", name="广州汇众服饰有限公司"))


@pytest.mark.parametrize("kind", messaging.MESSAGE_KINDS)
def test_every_message_kind_is_chinese_with_a_back_translation(offer, profile, kind):
    draft = messaging.draft_message(offer, profile, kind)
    assert has_chinese(draft["message_zh"]), "1688 sellers do not reliably read English"
    assert draft["message_en_backtranslation"]
    assert not has_chinese(draft["message_en_backtranslation"].replace(offer.title, ""))


def test_unknown_kind_is_rejected(offer, profile):
    with pytest.raises(ValueError, match="kind must be one of"):
        messaging.draft_message(offer, profile, "cajole")


def test_negotiation_states_the_target_price(offer, profile):
    draft = messaging.draft_message(offer, profile, "negotiate", quantity=200, target_price_cny=24.0)
    assert "24.00" in draft["message_zh"]
    assert draft["target_price_cny"] == 24.0


def test_below_moq_quantity_is_raised_as_a_question_not_assumed(offer, profile):
    # Quietly ordering under MOQ wastes the first exchange; asking does not.
    draft = messaging.draft_message(offer, profile, "inquiry", quantity=10)
    assert "起订量" in draft["message_zh"]
    assert "试单" in draft["message_zh"]


def test_extra_questions_are_appended_in_both_languages(offer, profile):
    draft = messaging.draft_message(offer, profile, "inquiry", extra_questions=["Do you ship DDP?"])
    assert "Do you ship DDP?" in draft["message_zh"]
    assert "Do you ship DDP?" in draft["message_en_backtranslation"]


def test_inquiry_asks_for_the_data_the_cost_model_needs(offer, profile):
    draft = messaging.draft_message(offer, profile, "inquiry")
    assert "克重" in draft["message_zh"]       # gsm
    assert "净重" in draft["message_zh"]       # unit weight, drives freight
    assert "外箱" in draft["message_zh"]       # carton dimensions


def test_draft_carries_a_safety_note(offer, profile):
    draft = messaging.draft_message(offer, profile, "inquiry")
    assert "deposit" in draft["note"]


def test_rfq_checklist_covers_the_whole_order_lifecycle(profile):
    checklist = messaging.rfq_checklist(profile)
    assert {"before_sampling", "before_bulk_order", "documentation_for_import"} <= set(checklist)
    assert all(checklist[section] for section in ("before_sampling", "before_bulk_order"))
