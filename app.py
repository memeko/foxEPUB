import io
import re
from functools import lru_cache
import zipfile
from flask import Flask, render_template, request, send_file, flash, redirect, url_for
from werkzeug.utils import secure_filename
from bs4 import BeautifulSoup, NavigableString

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB

VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
CONSONANTS = set("бвгджзйклмнпрстфхцчшщьъБВГДЖЗЙКЛМНПРСТФХЦЧШЩЬЪ")

ALLOWED_ONSET_2 = {
    "бл", "бр", "вл", "вр", "гл", "гр", "дл", "др", "жр", "зл", "зр",
    "кл", "кр", "пл", "пр", "сл", "см", "сн", "сп", "ст", "ск", "ср",
    "сф", "сх", "св", "шл", "шр", "тл", "тр", "фл", "фр", "хл", "хр",
    "чр", "вт", "гн", "мн", "мл", "мр", "нл", "нр",
}
ALLOWED_ONSET_3 = {"стр", "скр", "спр", "скл", "встр", "всп"}

WORD_RE = re.compile(r"[А-Яа-яЁё]+")
PUNCT_CHARS = r",;:!?()\[\]{}«»“”\"—–-"
TOKEN_RE = re.compile(rf"[А-Яа-яЁё]+|[{PUNCT_CHARS}]")


def _is_vowel(ch: str) -> bool:
    return ch in VOWELS


def _is_consonant(ch: str) -> bool:
    return ch in CONSONANTS


def split_russian_syllables(word: str) -> list[str]:
    vowel_positions = [i for i, ch in enumerate(word) if _is_vowel(ch)]
    if len(vowel_positions) <= 1:
        return [word]

    syllables = []
    start = 0
    for i in range(len(vowel_positions)):
        vpos = vowel_positions[i]
        if i == len(vowel_positions) - 1:
            syllables.append(word[start:])
            break

        next_vpos = vowel_positions[i + 1]
        cluster = word[vpos + 1:next_vpos]

        if not cluster:
            boundary = next_vpos
        else:
            lower_cluster = cluster.lower()
            if len(cluster) >= 2 and lower_cluster[0] == lower_cluster[1]:
                onset_len = len(cluster) - 1
            elif lower_cluster.endswith(("ь", "ъ")):
                onset_len = 1
            elif len(lower_cluster) >= 3 and lower_cluster[-3:] in ALLOWED_ONSET_3:
                onset_len = 3
            elif len(lower_cluster) >= 2 and lower_cluster[-2:] in ALLOWED_ONSET_2:
                onset_len = 2
            else:
                onset_len = 1

            boundary = next_vpos - onset_len

        if boundary <= start:
            boundary = next_vpos
        syllables.append(word[start:boundary])
        start = boundary

    return syllables


@lru_cache(maxsize=50000)
def first_syllable_parts(word: str) -> tuple[str, str]:
    syllables = split_russian_syllables(word)
    if not syllables:
        return word, ""
    first = syllables[0]
    rest = word[len(first):]
    return first, rest

@lru_cache(maxsize=50000)
def bionic_parts(word: str) -> tuple[str, str]:
    length = len(word)
    if length <= 3:
        n = length
    elif length <= 5:
        n = 2
    elif length <= 7:
        n = 2
    elif length <= 10:
        n = 3
    else:
        n = max(3, length // 3)
    return word[:n], word[n:]

def build_nodes_for_text(soup: BeautifulSoup, text: str, mode: str) -> list | None:
    if not TOKEN_RE.search(text):
        return None

    nodes = []
    i = 0
    for match in TOKEN_RE.finditer(text):
        if match.start() > i:
            nodes.append(NavigableString(text[i:match.start()]))

        token = match.group(0)
        if WORD_RE.fullmatch(token):
            if mode == "bionic":
                strong_text, rest_text = bionic_parts(token)
            else:
                strong_text, rest_text = first_syllable_parts(token)

            if strong_text:
                strong_tag = soup.new_tag("strong")
                strong_tag.string = strong_text
                nodes.append(strong_tag)
                if rest_text:
                    nodes.append(NavigableString(rest_text))
            else:
                nodes.append(NavigableString(token))
        else:
            span = soup.new_tag("span", **{"class": "punct"})
            span.string = token
            nodes.append(span)

        i = match.end()

    if i < len(text):
        nodes.append(NavigableString(text[i:]))

    return nodes


def replace_text_node(text_node, nodes: list) -> None:
    if not nodes:
        return
    first = nodes[0]
    text_node.replace_with(first)
    current = first
    for node in nodes[1:]:
        current.insert_after(node)
        current = node


def ensure_style(soup: BeautifulSoup) -> None:
    head = soup.head
    if not head:
        return
    if head.find("style", id="speedread-style"):
        return
    style = soup.new_tag("style", id="speedread-style", type="text/css")
    style.string = (
        "p { text-indent: 1.25em; margin-top: 0; margin-bottom: 0; }"
        " p + p { margin-top: 0; }"
        " .sr-gap { text-indent: 0; margin: 0 0 1em 0; }"
        " .punct { color: #666; opacity: 0.65; }"
    )
    head.append(style)


def process_html_bytes(data: bytes, mode: str) -> bytes:
    soup = BeautifulSoup(data, "html.parser")
    body = soup.body
    if not body:
        return data

    ensure_style(soup)

    paragraphs = body.find_all("p")
    for p in paragraphs:
        text_nodes = list(p.find_all(string=True))
        for text_node in text_nodes:
            parent = text_node.parent
            if parent and parent.name in {"script", "style"}:
                continue
            original = str(text_node)
            nodes = build_nodes_for_text(soup, original, mode)
            if nodes:
                replace_text_node(text_node, nodes)

    # Insert empty paragraph gaps between paragraphs to enforce visible blank lines in readers
    paragraphs = body.find_all("p")
    for p in paragraphs:
        next_sibling = p.find_next_sibling()
        if next_sibling and next_sibling.name == "p" and "sr-gap" in next_sibling.get("class", []):
            continue
        gap = soup.new_tag("p")
        gap["class"] = ["sr-gap"]
        gap.append(soup.new_string("\u00a0"))
        p.insert_after(gap)

    return str(soup).encode("utf-8")


def process_epub(epub_bytes: bytes, mode: str) -> bytes:
    input_io = io.BytesIO(epub_bytes)
    output_io = io.BytesIO()

    with zipfile.ZipFile(input_io, "r") as zin, zipfile.ZipFile(output_io, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            lower = info.filename.lower()
            if lower.endswith((".xhtml", ".html", ".htm")):
                data = process_html_bytes(data, mode)

            new_info = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            new_info.compress_type = info.compress_type
            new_info.external_attr = info.external_attr
            new_info.internal_attr = info.internal_attr
            new_info.flag_bits = info.flag_bits
            new_info.create_system = info.create_system
            new_info.create_version = info.create_version
            new_info.extract_version = info.extract_version
            new_info.volume = info.volume
            new_info.comment = info.comment
            new_info.extra = info.extra
            zout.writestr(new_info, data)

    output_io.seek(0)
    return output_io.read()


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/convert")
def convert():
    if "epub" not in request.files:
        flash("Файл не найден в запросе")
        return redirect(url_for("index"))

    file = request.files["epub"]
    if not file or file.filename == "":
        flash("Выберите EPUB-файл")
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".epub"):
        flash("Нужен файл с расширением .epub")
        return redirect(url_for("index"))

    epub_bytes = file.read()
    mode = request.form.get("mode", "syllable")
    try:
        out_bytes = process_epub(epub_bytes, mode)
    except zipfile.BadZipFile:
        flash("Файл не похож на EPUB (поврежденный ZIP)")
        return redirect(url_for("index"))

    out_name = filename[:-5] + "-speedread.epub"
    return send_file(
        io.BytesIO(out_bytes),
        mimetype="application/epub+zip",
        as_attachment=True,
        download_name=out_name,
    )


if __name__ == "__main__":
    app.run(debug=True)
