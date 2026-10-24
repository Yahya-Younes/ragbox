from ragbox.chunking import chunk_pages, clean_text


def test_clean_text_rejoins_hyphenated_words_and_spaces():
    assert clean_text("infor-\nmation   is\r\n\n\n\nhere") == "information is\n\nhere"


def test_chunks_respect_size_and_keep_pages():
    text = ("Sentence number one is here. " * 40) + "\n\n" + ("Another paragraph sentence. " * 60)
    chunks = chunk_pages([(3, text), (4, "Tiny page.")], size=300, overlap=80)
    assert all(len(c) <= 300 for _, c in chunks)
    assert chunks[0][0] == 3 and chunks[-1] == (4, "Tiny page.")


def test_overlap_repeats_previous_tail():
    paras = "\n\n".join(f"Paragraph {i} " + "word " * 20 for i in range(10))
    chunks = chunk_pages([(None, paras)], size=300, overlap=150)
    first_last_piece = chunks[0][1].split("\n")[-1]
    assert chunks[1][1].startswith(first_last_piece)


def test_very_long_word_is_hard_split():
    chunks = chunk_pages([(None, "x" * 1000)], size=200, overlap=0)
    assert len(chunks) == 5 and all(len(c) == 200 for _, c in chunks)
