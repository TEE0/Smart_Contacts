from sourcing1688 import lexicon


def test_translates_apparel_brief_to_trade_chinese():
    zh = lexicon.translate_phrase("heavyweight oversized cotton t-shirt for men")
    assert "短袖T恤" in zh
    assert "重磅" in zh and "宽松" in zh and "纯棉" in zh and "男装" in zh


def test_longest_match_wins_over_substring():
    # "long sleeve tee" must not be shredded into "tee".
    matched = {phrase for phrase, _group, _zh in lexicon.extract_terms("long sleeve tee")}
    assert "long sleeve tee" in matched
    assert "tee" not in matched


def test_adjacent_terms_both_match():
    # Regression: a char-offset matcher let each match consume its neighbour's
    # leading space, so consecutive words silently blocked each other.
    matched = {p for p, _g, _z in lexicon.extract_terms("womens winter puffer jacket")}
    assert {"womens", "winter", "puffer jacket"} <= matched


def test_hyphenated_and_accented_input_is_normalised():
    assert "短袖T恤" in lexicon.translate_phrase("t-shirt")
    assert "圈圈纱" in lexicon.translate_phrase("bouclé knit")


def test_morphological_variants_resolve():
    assert "刺绣" in lexicon.translate_phrase("embroidered logo")
    assert "夹克" in lexicon.translate_phrase("denim jackets")


def test_duplicate_morphemes_are_collapsed():
    # 纯棉纯棉T恤 is a worse 1688 query than 纯棉T恤.
    zh = lexicon.join_terms(["纯棉", "纯棉T恤", "T恤"])
    assert zh == "纯棉T恤"


def test_chinese_input_passes_through_untouched():
    assert lexicon.translate_phrase("女装冬季羽绒服") == "女装冬季羽绒服"
    assert lexicon.unknown_terms("女装冬季羽绒服") == []


def test_unknown_terms_reports_only_genuinely_unmapped_words():
    unknown = lexicon.unknown_terms("heavyweight cotton tee with lurex piping")
    assert "lurex" in unknown
    assert "cotton" not in unknown and "tee" not in unknown


def test_alternates_offer_different_garment_names():
    alts = lexicon.alternates("cotton hoodie")
    assert alts, "a hoodie should have alternate Chinese names"
    assert any("帽衫" in a or "卫衣" in a for a in alts)
