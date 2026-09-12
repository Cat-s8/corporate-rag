from pathlib import Path
import json
import re

import pymupdf


PDF_DIR = Path("data/raw_pdf")
OUTPUT_FILE = Path("data/processed/chunks_pdf.json")

CHUNK_SIZE = 1800
CHUNK_OVERLAP = 250


# Заголовки вида:
# 1 Общие положения
# 1.1 ...
# 3.1.1 ...
# 3.1.1.1 ...
HEADING_RE = re.compile(
    r"^(\d+(?:\.\d+){0,4})\s+(.+)$"
)

PAGE_NUMBER_RE = re.compile(
    r"Стр\.\s*(\d+)",
    re.IGNORECASE
)

FIGURE_RE = re.compile(
    r"^Рисунок\s+\d+(?:\.\d+)?\s*[–—\-]"
)

TABLE_RE = re.compile(
    r"^Таблица\s+\d+(?:\.\d+)?\s*[–—\-]"
)


# Эти разделы полезны для поиска
SPECIAL_FRONT_SECTIONS = {
    "Аннотация",
    "Перечень сокращений",
    "Перечень терминов",
}


# Эти страницы не индексируем как знания
SKIP_FRONT_SECTIONS = {
    "Содержание",
    "Перечень рисунков",
    "Перечень таблиц",
    "Перечень приложений",
    "Перечень приложений, представленных отдельными томами",
}


SKIP_END_SECTIONS = {
    "Согласовано",
    "Лист регистрации изменений",
}


def normalize_line(text):
    """
    Нормализация строки.
    """

    text = text.replace("\u00ad", "")
    text = text.replace("", "–")
    text = text.replace("￾", "")
    text = text.replace("\xa0", " ")

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def extract_document_info(document):
    """
    Пытаемся получить название документа
    и его код из первых страниц PDF.
    """

    sample_parts = []

    for page_number in range(
        min(4, len(document))
    ):

        sample_parts.append(
            document[page_number].get_text(
                "text",
                sort=True
            )
        )

    sample = "\n".join(
        sample_parts
    )

    title_match = re.search(
        r"Название документа:\s*(.*?)\s*Код документа",
        sample,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if title_match:

        document_title = normalize_line(
            title_match.group(1)
        )

    else:

        document_title = ""

    code_match = re.search(
        r"Код документа:?\s*([^\n]+)",
        sample,
        flags=re.IGNORECASE,
    )

    if code_match:

        document_code = normalize_line(
            code_match.group(1)
        )

    else:

        document_code = ""

    return (
        document_title,
        document_code
    )


def extract_valid_headings(document):
    """
    Извлекает из содержания:
    номер раздела -> название раздела.

    Пример:
    {
        "1": "Общие положения",
        "1.2": "Функциональные роли, выполняемые пользователем",
        "2.2": "Подготовительные действия"
    }
    """

    headings = {}

    in_contents = False

    heading_re = re.compile(
        r"^(\d+(?:\.\d+){0,4})\s+(.+?)(?:\.{2,}|\s+\d+\s*$)"
    )

    for page in document:

        # Содержание находится в начале документов.
        if page.number > 12:
            break

        text = page.get_text(
            "text",
            sort=True
        )

        lines = [
            normalize_line(line)
            for line in text.splitlines()
            if normalize_line(line)
        ]

        for line in lines:

            if line == "Содержание":
                in_contents = True
                continue

            if not in_contents:
                continue

            match = heading_re.match(line)

            if not match:
                continue

            number = match.group(1)

            title = match.group(2).strip()

            # Убираем точки-лидеры содержания.
            title = re.sub(
                r"\.{2,}.*$",
                "",
                title
            ).strip()

            if title:
                headings[number] = title

    return headings

def extract_page_body(page):
    """
    Возвращает:
    - номер страницы внутри документа;
    - основной текст страницы
      без повторяющейся шапки.
    """

    raw_text = page.get_text(
        "text",
        sort=True
    )

    page_match = PAGE_NUMBER_RE.search(
        raw_text
    )

    if page_match is None:

        return None, ""

    document_page = int(
        page_match.group(1)
    )

    # Всё до "Стр. N" считаем
    # повторяющейся шапкой страницы.
    body = raw_text[
        page_match.end():
    ]

    return (
        document_page,
        body
    )


def prepare_lines(text):
    """
    Подготавливает строки страницы.
    """

    lines = []

    for raw_line in text.splitlines():

        line = normalize_line(
            raw_line
        )

        if not line:
            continue

        # Подписи рисунков можно не индексировать
        # как основной текст.
        if FIGURE_RE.match(line):
            continue

        # Название таблицы тоже не обязательно
        # использовать как самостоятельный текст.
        # Содержимое таблицы при этом остается.
        if TABLE_RE.match(line):
            continue

        lines.append(
            line
        )

    return lines


def get_first_line(lines):

    if not lines:
        return ""

    return lines[0]


def update_hierarchy(
    hierarchy,
    number,
    title
):
    """
    Обновляет текущую иерархию документа.
    """

    depth = (
        number.count(".") + 1
    )

    full_heading = (
        f"{number} {title}"
    )

    if depth == 1:

        hierarchy["section"] = (
            full_heading
        )

        hierarchy["subsection"] = ""
        hierarchy["point"] = ""
        hierarchy["subpoint"] = ""

    elif depth == 2:

        hierarchy["subsection"] = (
            full_heading
        )

        hierarchy["point"] = ""
        hierarchy["subpoint"] = ""

    elif depth == 3:

        hierarchy["point"] = (
            full_heading
        )

        hierarchy["subpoint"] = ""

    else:

        hierarchy["subpoint"] = (
            full_heading
        )


def collect_segments(
    lines,
    hierarchy,
    valid_headings
):
    """
    Делит страницу по реальным
    структурным заголовкам.

    Строка считается заголовком только
    если её номер найден в содержании PDF.
    """

    segments = []

    current_lines = []

    current_metadata = (
        hierarchy.copy()
    )

    i = 0

    while i < len(lines):

        line = lines[i]

        heading_match = (
            HEADING_RE.match(line)
        )

        if heading_match:

            number = heading_match.group(1)
            candidate_title = (
                heading_match.group(2)
                .strip()
                .lower()
            )
            if valid_headings:

                expected_title = (
                    valid_headings.get(number)
                )

                if expected_title is None:

                    heading_match = None

                else:

                    expected_title = (
                        expected_title.lower()
                    )

                    # Заголовок должен совпадать
                    # не только по номеру,
                    # но и по началу названия.
                    compare_length = min(
                        25,
                        len(expected_title),
                        len(candidate_title)
                    )

                    if compare_length > 0:

                        if (
                            candidate_title[:compare_length]
                            != expected_title[:compare_length]
                        ):
                            heading_match = None

        if heading_match:

            if current_lines:

                segments.append({
                    "metadata":
                        current_metadata.copy(),

                    "text":
                        "\n".join(
                            current_lines
                        ).strip(),
                })

                current_lines = []

            number = (
                heading_match.group(1)
            )

            title = (
                heading_match.group(2)
                .strip()
            )

            # Заголовки в PDF могут переноситься
            # на следующую строку.
            j = i + 1

            while j < len(lines):

                next_line = lines[j]

                # Если следующая строка сама
                # является настоящим заголовком,
                # останавливаемся.
                next_heading = (
                    HEADING_RE.match(
                        next_line
                    )
                )

                if next_heading:

                    next_number = (
                        next_heading.group(1)
                    )

                    if next_number in valid_headings:
                        break

                # Продолжение длинного заголовка.
                if (
                    next_line
                    and next_line[0].islower()
                    and len(title) < 220
                ):

                    title += (
                        " " + next_line
                    )

                    j += 1

                else:
                    break

            update_hierarchy(
                hierarchy,
                number,
                title
            )

            current_metadata = (
                hierarchy.copy()
            )

            current_lines.append(
                f"{number} {title}"
            )

            i = j
            continue

        current_lines.append(
            line
        )

        i += 1

    if current_lines:

        segments.append({
            "metadata":
                current_metadata.copy(),

            "text":
                "\n".join(
                    current_lines
                ).strip(),
        })

    return segments


def split_long_text(text):
    """
    Делит только слишком длинный
    смысловой сегмент.

    Короткие пункты остаются целыми.
    """

    text = text.strip()

    if len(text) <= CHUNK_SIZE:

        return [text]

    result = []

    start = 0

    while start < len(text):

        end = min(
            start + CHUNK_SIZE,
            len(text)
        )

        if end < len(text):

            # Сначала пытаемся разрезать
            # по переносу строки.
            split_position = text.rfind(
                "\n",
                start + CHUNK_SIZE // 2,
                end
            )

            # Если переноса нет,
            # режем по пробелу.
            if split_position == -1:

                split_position = (
                    text.rfind(
                        " ",
                        start
                        + CHUNK_SIZE // 2,
                        end
                    )
                )

            if split_position != -1:

                end = (
                    split_position
                )

        chunk = text[
            start:end
        ].strip()

        if chunk:

            result.append(
                chunk
            )

        if end >= len(text):
            break

        next_start = max(
            end - CHUNK_OVERLAP,
            start + 1
        )

        # Не начинаем чанк
        # посередине слова.
        while (
            next_start < len(text)
            and not text[
                next_start
            ].isspace()
        ):

            next_start += 1

        start = next_start

    return result


def should_skip_front_page(
    first_line
):
    """
    Определяет служебные страницы,
    которые не должны участвовать
    в retrieval.
    """

    for section in SKIP_FRONT_SECTIONS:

        if first_line.startswith(
            section
        ):
            return True

    return False


def process_pdf(pdf_path):

    print()
    print(
        f"Обработка: "
        f"{pdf_path.name}"
    )

    document = pymupdf.open(
        pdf_path
    )

    (
        document_title,
        document_code
    ) = extract_document_info(
        document
    )

    valid_headings = (
        extract_valid_headings(
            document
        )
    )

    print(
        f"Страниц PDF: "
        f"{len(document)}"
    )

    print(
        f"Найдено структурных "
        f"заголовков: "
        f"{len(valid_headings)}"
    )

    hierarchy = {
        "section": "",
        "subsection": "",
        "point": "",
        "subpoint": "",
    }

    chunks = []

    main_content_started = False

    front_section_active = False

    indexed_pages = 0
    skipped_pages = 0

    for pdf_index, page in enumerate(
        document,
        start=1
    ):

        (
            document_page,
            body
        ) = extract_page_body(
            page
        )

        # Например, титульная страница
        # без маркировки "Стр. N".
        if document_page is None:

            skipped_pages += 1
            continue

        lines = prepare_lines(
            body
        )

        if not lines:

            skipped_pages += 1
            continue

        first_line = get_first_line(
            lines
        )

        # ---------------------------------
        # Вводная часть документа
        # ---------------------------------

        if not main_content_started:

            # Начало основной главы
            first_heading = (
                HEADING_RE.match(
                    first_line
                )
            )

            if first_heading:

                first_number = (
                    first_heading.group(1)
                )

                if (
                    first_number == "1"
                    and (
                        not valid_headings
                        or first_number
                        in valid_headings
                    )
                ):

                    main_content_started = True
                    front_section_active = False

            if not main_content_started:

                if (
                    first_line
                    in SPECIAL_FRONT_SECTIONS
                ):

                    hierarchy = {
                        "section": first_line,
                        "subsection": "",
                        "point": "",
                        "subpoint": "",
                    }

                    front_section_active = True

                elif should_skip_front_page(
                    first_line
                ):

                    front_section_active = False

                    skipped_pages += 1
                    continue

                elif not front_section_active:

                    skipped_pages += 1
                    continue

        # ---------------------------------
        # Служебные страницы в конце
        # ---------------------------------

        if first_line in SKIP_END_SECTIONS:

            skipped_pages += 1
            continue

        indexed_pages += 1

        segments = collect_segments(
            lines,
            hierarchy,
            valid_headings
        )

        for segment in segments:

            segment_text = (
                segment["text"]
            )

            if not segment_text:
                continue

            pieces = split_long_text(
                segment_text
            )

            for piece_number, piece in enumerate(
                pieces,
                start=1
            ):

                if len(piece) < 40:
                    continue

                chunk = {
                    "source":
                        pdf_path.name,

                    "document_title":
                        document_title,

                    "document_code":
                        document_code,

                    # Физическая страница
                    # файла PDF.
                    "pdf_page":
                        pdf_index,

                    # Номер страницы,
                    # напечатанный в документе.
                    "document_page":
                        document_page,

                    "section":
                        segment[
                            "metadata"
                        ]["section"],

                    "subsection":
                        segment[
                            "metadata"
                        ]["subsection"],

                    "point":
                        segment[
                            "metadata"
                        ]["point"],

                    "subpoint":
                        segment[
                            "metadata"
                        ]["subpoint"],

                    # Если большой пункт пришлось
                    # поделить на несколько частей.
                    "chunk_part":
                        piece_number,

                    "text":
                        piece,
                }

                chunks.append(
                    chunk
                )

    document.close()

    print(
        f"Проиндексировано страниц: "
        f"{indexed_pages}"
    )

    print(
        f"Пропущено страниц: "
        f"{skipped_pages}"
    )

    print(
        f"Создано чанков: "
        f"{len(chunks)}"
    )

    return chunks


def main():

    if not PDF_DIR.exists():

        print(
            f"Ошибка: папка "
            f"{PDF_DIR} не найдена."
        )

        return

    pdf_files = sorted(
        PDF_DIR.glob("*.pdf")
    )

    if not pdf_files:

        print(
            f"PDF-файлы в "
            f"{PDF_DIR} не найдены."
        )

        return

    print("=" * 70)
    print("PDF INGESTION")
    print("=" * 70)

    all_chunks = []

    for pdf_path in pdf_files:

        document_chunks = (
            process_pdf(
                pdf_path
            )
        )

        all_chunks.extend(
            document_chunks
        )

    # Присваиваем общий уникальный ID
    for chunk_id, chunk in enumerate(
        all_chunks
    ):

        chunk["chunk_id"] = (
            chunk_id
        )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            all_chunks,
            file,
            ensure_ascii=False,
            indent=2
        )

    print()
    print("=" * 70)
    print("ГОТОВО")
    print("=" * 70)

    print(
        f"Всего чанков: "
        f"{len(all_chunks)}"
    )

    print(
        f"Результат: "
        f"{OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()