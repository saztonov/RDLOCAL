"""Конвертация HTML таблиц в Markdown."""
import re


def clean_cell_text(text: str) -> str:
    """Очистить текст ячейки таблицы - заменить переносы на пробелы."""
    text = re.sub(r'\s*\n\s*', ' ', text)
    text = re.sub(r' +', ' ', text)
    return text.strip()


def parse_cell_span(cell_tag: str) -> tuple:
    """Извлечь colspan и rowspan из тега ячейки."""
    colspan_match = re.search(r'colspan\s*=\s*["\']?(\d+)', cell_tag, re.IGNORECASE)
    rowspan_match = re.search(r'rowspan\s*=\s*["\']?(\d+)', cell_tag, re.IGNORECASE)
    colspan = int(colspan_match.group(1)) if colspan_match else 1
    rowspan = int(rowspan_match.group(1)) if rowspan_match else 1
    return colspan, rowspan


def table_to_markdown(table_html: str) -> str:
    """Конвертировать таблицу HTML в Markdown (включая сложные таблицы с colspan/rowspan)."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.DOTALL)
    if not rows:
        return ""

    # Парсим все строки с учетом colspan/rowspan
    parsed_rows = []
    rowspan_tracker: dict[int, tuple[int, str]] = {}  # {col_index: (remaining_rows, text)}

    for row_html in rows:
        # Находим все ячейки с их тегами
        cell_matches = re.findall(r"<(t[hd][^>]*)>(.*?)</t[hd]>", row_html, flags=re.DOTALL)
        if not cell_matches:
            continue

        row_cells = []
        col_idx = 0
        cell_iter = iter(cell_matches)

        while True:
            # Проверяем, есть ли активный rowspan для текущей колонки
            if col_idx in rowspan_tracker:
                remaining, text = rowspan_tracker[col_idx]
                row_cells.append("")  # Пустая ячейка для объединенной строки
                if remaining <= 1:
                    del rowspan_tracker[col_idx]
                else:
                    rowspan_tracker[col_idx] = (remaining - 1, text)
                col_idx += 1
                continue

            # Берем следующую ячейку из HTML
            try:
                cell_tag, cell_content = next(cell_iter)
            except StopIteration:
                break

            colspan, rowspan = parse_cell_span(cell_tag)
            text = re.sub(r"<br\s*/?>", " ", cell_content, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", "", text)
            text = clean_cell_text(text)

            # Добавляем ячейку
            row_cells.append(text)

            # Регистрируем rowspan для последующих строк
            if rowspan > 1:
                rowspan_tracker[col_idx] = (rowspan - 1, text)

            col_idx += 1

            # Добавляем пустые ячейки для colspan
            for _ in range(colspan - 1):
                row_cells.append("")
                col_idx += 1

        # Обрабатываем оставшиеся rowspan'ы в конце строки
        while col_idx in rowspan_tracker:
            remaining, text = rowspan_tracker[col_idx]
            row_cells.append("")
            if remaining <= 1:
                del rowspan_tracker[col_idx]
            else:
                rowspan_tracker[col_idx] = (remaining - 1, text)
            col_idx += 1

        if row_cells:
            parsed_rows.append(row_cells)

    if not parsed_rows:
        return ""

    # Определяем максимальное количество колонок
    max_cols = max(len(row) for row in parsed_rows)

    # Выравниваем все строки по максимальному количеству колонок
    for row in parsed_rows:
        while len(row) < max_cols:
            row.append("")

    # Формируем markdown таблицу
    md_rows = []
    for i, row in enumerate(parsed_rows):
        # Экранируем pipe в содержимом ячеек
        escaped_cells = [cell.replace("|", "\\|") for cell in row]
        md_rows.append("| " + " | ".join(escaped_cells) + " |")

        # Добавляем разделитель после первой строки (заголовок)
        if i == 0:
            md_rows.append("|" + "|".join(["---"] * max_cols) + "|")

    return "\n".join(md_rows)
