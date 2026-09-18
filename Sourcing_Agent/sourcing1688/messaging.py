"""Supplier contact drafting, in Chinese.

The shortlist is worthless if the buyer cannot open the conversation. 1688
sellers answer 阿里旺旺 messages in Chinese, largely ignore English, and respond
far better to a message that reads like a trade buyer than a translated
consumer enquiry.

These are templates, not model output: a negotiation message must be predictable
and must never invent a commitment the buyer did not authorise. Each draft comes
with an English back-translation so the buyer always knows exactly what is being
sent in their name.
"""

from __future__ import annotations

from typing import Any

from .models import Offer

MESSAGE_KINDS = ("inquiry", "sample", "negotiate", "customization", "qc", "followup")


def _quantity_line(quantity: int, moq: int) -> tuple[str, str]:
    if moq and quantity < moq:
        return (
            f"我们首单计划采购 {quantity} 件，贵司起订量是 {moq} 件，首单能否按 {quantity} 件试单？",
            f"We plan {quantity} units for a first order against your {moq}-unit MOQ - can you accept {quantity} as a trial?",
        )
    return (
        f"我们首单计划采购 {quantity} 件，后续按销售情况返单。",
        f"We plan {quantity} units for the first order, with repeat orders based on sell-through.",
    )


def draft_message(
    offer: Offer,
    profile: dict[str, Any],
    kind: str = "inquiry",
    *,
    quantity: int | None = None,
    target_price_cny: float | None = None,
    extra_questions: list[str] | None = None,
) -> dict[str, Any]:
    """Return a Chinese message plus its English back-translation."""
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"kind must be one of {', '.join(MESSAGE_KINDS)}")

    commercial = profile.get("commercial", {})
    identity = profile.get("identity", {})
    materials = profile.get("materials", {})
    quantity = int(quantity or commercial.get("ideal_order_quantity") or 1)
    listed_price = offer.price_at_quantity(quantity)
    target = float(target_price_cny or commercial.get("target_unit_price_cny") or 0)

    qty_zh, qty_en = _quantity_line(quantity, offer.moq)
    title = offer.title or offer.offer_id

    zh: list[str] = ["您好，我们是海外服装采购方，看中贵司这款产品：", f"【{title}】（货号 {offer.offer_id}）"]
    en: list[str] = ["Hello - we are an overseas apparel buyer interested in this product:", f"[{title}] (item {offer.offer_id})"]

    if kind == "inquiry":
        zh += [
            qty_zh,
            "想确认以下几点：",
            "1. 这款的实际面料成分和克重是多少？能否提供成分检测报告？",
            "2. 起订量、阶梯价格，以及大货生产周期是多少天？",
            "3. 可选颜色和尺码有哪些？是否支持按我们的尺码表生产？",
            "4. 一件的净重和外箱尺寸/毛重是多少？（我们需要核算国际运费）",
            "5. 是否支持打样？打样费和样品周期？",
        ]
        en += [
            qty_en,
            "Please confirm:",
            "1. Actual fabric composition and gsm - can you provide a composition test report?",
            "2. MOQ, price ladder, and bulk production lead time in days?",
            "3. Available colours and sizes - can you produce to our own size chart?",
            "4. Net weight per piece and carton dimensions / gross weight (needed to cost freight)?",
            "5. Do you offer samples, at what cost and lead time?",
        ]
    elif kind == "sample":
        zh += [
            "我们想先订购样品确认品质。",
            "1. 样品费用和运费是多少？大货下单后样品费能否退还或抵扣？",
            "2. 样品几天可以发出？",
            "3. 请按现货颜色各发一件，我们需要核对面料手感和做工。",
            "4. 请随样品提供面料成分标签照片。",
        ]
        en += [
            "We would like to order samples to verify quality first.",
            "1. Sample cost and shipping - is the sample fee refundable or creditable against a bulk order?",
            "2. How many days until samples ship?",
            "3. Please send one piece per in-stock colour so we can check hand-feel and workmanship.",
            "4. Please include a photo of the fabric composition label with the samples.",
        ]
    elif kind == "negotiate":
        gap = f"目前标价是 ¥{listed_price:.2f}/件，" if listed_price else ""
        zh += [
            qty_zh,
            f"{gap}我们的目标价是 ¥{target:.2f}/件（含税不含运费）。",
            "如果价格可以谈拢，我们可以：",
            f"1. 首单直接下 {quantity} 件，并在三个月内安排返单；",
            "2. 接受贵司现有配色，减少备料成本；",
            "3. 付款方式可以按贵司常规（预付定金＋发货前结清）。",
            "请问这个数量下最好的价格是多少？如果达不到，差在哪里（面料/工艺/数量）？",
        ]
        en += [
            qty_en,
            f"{'Listed at ¥%.2f/unit. ' % listed_price if listed_price else ''}Our target is ¥{target:.2f}/unit (tax included, freight excluded).",
            "If we can agree on price we can:",
            f"1. Place {quantity} units immediately and reorder within three months;",
            "2. Accept your existing colourways to reduce your material cost;",
            "3. Pay on your standard terms (deposit plus balance before shipment).",
            "What is your best price at this quantity - and if you cannot meet it, what is driving the gap (fabric, process, volume)?",
        ]
    elif kind == "customization":
        zh += [
            "我们需要贴牌定制，请确认：",
            "1. 是否支持定制 logo（刺绣/丝印/织标）？定制起订量是多少？",
            "2. 是否可以定制主唛、洗唛、吊牌和包装袋？",
            "3. 定制的开版费/版费是多少？是否可在返单时减免？",
            "4. 可否按我们提供的尺码表和配色卡生产？色差范围如何控制？",
            "5. 定制大货的生产周期是多少天？",
        ]
        en += [
            "We need private-label production. Please confirm:",
            "1. Do you support custom logos (embroidery / screen print / woven label), and what is the MOQ for customisation?",
            "2. Can you customise main label, care label, hang tags and polybag?",
            "3. What are the setup / mould fees, and are they waived on reorders?",
            "4. Can you produce to our size chart and colour card, and how is colour deviation controlled?",
            "5. What is the production lead time for a customised bulk order?",
        ]
    elif kind == "qc":
        zh += [
            "下单前需要确认品质控制安排：",
            "1. 大货出货前是否接受第三方验货（我们会安排验货公司上门）？",
            "2. 次品率如何界定和处理？发现质量问题如何补偿？",
            "3. 是否可以提供大货生产中的图片或视频？",
            "4. 出货前请提供装箱单（每箱件数、箱规、毛净重）。",
        ]
        en += [
            "Before ordering we need to agree quality control:",
            "1. Do you accept third-party inspection before shipment (we would send an inspection company)?",
            "2. How is the defect rate defined and handled, and what is the remedy if defects are found?",
            "3. Can you provide photos or video during bulk production?",
            "4. Please provide a packing list before shipment (pieces per carton, carton size, gross and net weight).",
        ]
    else:  # followup
        zh += [
            "之前咨询过这款产品，想跟进一下报价和样品安排，方便的话请回复。",
            "如果这款没有现货，也可以推荐类似款式，我们的要求是：",
            f"面料 {', '.join(materials.get('required_any') or ['不限'])}，"
            f"目标价 ¥{target:.2f}/件，采购数量 {quantity} 件。",
        ]
        en += [
            "Following up on my earlier enquiry about quotation and samples - please reply when convenient.",
            "If this item is unavailable, similar styles are welcome. Our requirements:",
            f"fabric {', '.join(materials.get('required_any') or ['any'])}, "
            f"target ¥{target:.2f}/unit, order quantity {quantity} units.",
        ]

    for question in extra_questions or []:
        zh.append(f"补充问题：{question}")
        en.append(f"Additional question: {question}")

    destination = identity.get("destination_country") or ""
    if destination and kind in {"inquiry", "negotiate", "customization"}:
        zh.append(f"我们发货到 {destination}，需要正规发票和装箱单用于清关。")
        en.append(f"We ship to {destination} and need a proper invoice and packing list for customs clearance.")

    zh.append("谢谢！期待回复。")
    en.append("Thank you - looking forward to your reply.")

    return {
        "kind": kind,
        "offer_id": offer.offer_id,
        "supplier": offer.supplier.name,
        "quantity": quantity,
        "target_price_cny": round(target, 2) if target else None,
        "message_zh": "\n".join(zh),
        "message_en_backtranslation": "\n".join(en),
        "send_via": "阿里旺旺 (Aliwangwang) on the listing page, or the 'Contact Supplier' button",
        "note": (
            "Sent as written it commits you to nothing beyond an enquiry. Do not paste a deposit amount or "
            "shipping address into a first message."
        ),
    }


def rfq_checklist(profile: dict[str, Any]) -> dict[str, Any]:
    """What to have settled before money moves."""
    commercial = profile.get("commercial", {})
    return {
        "before_sampling": [
            "Fabric composition and gsm confirmed in writing (not just the listing text).",
            "Price ladder confirmed for your actual quantity, stating tax-inclusive or not.",
            "Sample cost, sample lead time, and whether the fee is creditable against bulk.",
            "Whether the listing photo is the supplier's own production or a stock image.",
        ],
        "before_bulk_order": [
            "Signed size chart and colour card, with an agreed colour-deviation tolerance.",
            "Production lead time with a date, plus the penalty or remedy for late shipment.",
            "Carton dimensions, pieces per carton, gross and net weight (freight depends on it).",
            "Defect-rate definition and remedy.",
            "Third-party inspection right before shipment.",
            "Payment terms - avoid paying 100% up front on a first order.",
        ],
        "documentation_for_import": [
            "Commercial invoice and packing list.",
            "Fabric composition declaration and, where required, care-labelling compliance.",
            f"Any certification your profile requires: {', '.join(profile.get('supplier', {}).get('required_certifications') or ['none set'])}.",
            "HS code agreed with your customs broker before shipment, not after.",
        ],
        "your_settings": {
            "sample_first": bool(commercial.get("needs_sample_first")),
            "customization_required": bool(commercial.get("needs_customization")),
            "planned_quantity": commercial.get("ideal_order_quantity"),
        },
    }
