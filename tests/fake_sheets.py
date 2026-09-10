"""Dubles da Google Sheets API para os testes -- nada sai para a rede.

Alem de devolver dados canonicos, o duble FALHA se alguem tentar chamar
`spreadsheets().batchUpdate`, que a politica do CRM proibe.
"""

from __future__ import annotations

import re

_RANGE_RE = re.compile(
    r"^(?:'(?P<quoted>(?:[^']|'')+)'|(?P<plain>[^'!]+))!(?P<ref>.+)$"
)
_PART_RE = re.compile(r"^\$?(?P<col>[A-Za-z]{1,3})?\$?(?P<row>\d+)?$")


def _col_to_idx(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _idx_to_col(idx: int) -> str:
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def parse(a1_range: str) -> tuple[str, int | None, int | None, int | None, int | None]:
    """(aba, linha_ini, linha_fim, col_ini, col_fim) base 0, fim inclusivo, None = aberto."""
    m = _RANGE_RE.match(a1_range.strip())
    if not m:
        raise AssertionError(f"intervalo nao reconhecido pelo duble: {a1_range!r}")
    tab = (m.group("quoted") or "").replace("''", "'") or m.group("plain")
    parts = m.group("ref").split(":")
    bounds = []
    for part in parts:
        pm = _PART_RE.match(part)
        if not pm:
            raise AssertionError(f"referencia nao reconhecida: {part!r}")
        col = _col_to_idx(pm.group("col")) if pm.group("col") else None
        row = int(pm.group("row")) - 1 if pm.group("row") else None
        bounds.append((col, row))
    if len(bounds) == 1:
        (c, r) = bounds[0]
        return tab, r, r, c, c
    (c1, r1), (c2, r2) = bounds
    return tab, r1, r2, c1, c2


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeValues:
    def __init__(self, owner: "FakeService"):
        self._owner = owner

    def get(self, spreadsheetId, range, valueRenderOption=None, dateTimeRenderOption=None):
        self._owner.assert_spreadsheet(spreadsheetId)
        self._owner.calls.append(("values.get", range))
        tab, r1, r2, c1, c2 = parse(range)
        grid = self._owner.data.get(tab, [])
        r1 = 0 if r1 is None else r1
        r2 = len(grid) - 1 if r2 is None else r2
        out = []
        for row in grid[r1 : r2 + 1]:
            lo = 0 if c1 is None else c1
            hi = len(row) - 1 if c2 is None else c2
            out.append(list(row[lo : hi + 1]))
        while out and not any(str(c).strip() for c in out[-1]):
            out.pop()
        return _Exec({"values": out} if out else {})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self._owner.assert_spreadsheet(spreadsheetId)
        assert valueInputOption == "RAW", valueInputOption
        assert insertDataOption == "INSERT_ROWS", insertDataOption
        rows = body["values"]
        assert len(rows) == 1, "o gateway deve gravar exatamente uma linha"
        tab, *_ = parse(range)
        grid = self._owner.data.setdefault(tab, [])
        grid.append(list(rows[0]))
        written = len(grid)  # 1-based
        last_col = _idx_to_col(len(rows[0]) - 1)
        updated = f"{tab}!A{written}:{last_col}{written}"
        self._owner.calls.append(("values.append", updated))
        return _Exec(
            {
                "spreadsheetId": spreadsheetId,
                "updates": {
                    "updatedRange": updated,
                    "updatedRows": 1,
                    "updatedColumns": len(rows[0]),
                    "updatedCells": len(rows[0]),
                },
            }
        )


class FakeSpreadsheets:
    def __init__(self, owner: "FakeService"):
        self._owner = owner
        self._values = FakeValues(owner)

    def values(self):
        return self._values

    def get(self, spreadsheetId, fields=None, ranges=None, includeGridData=False):
        self._owner.assert_spreadsheet(spreadsheetId)
        if includeGridData:
            self._owner.calls.append(("grid", ranges[0]))
            tab, r1, r2, c1, c2 = parse(ranges[0])
            grid = self._owner.data.get(tab, [])
            row = grid[r1] if r1 is not None and r1 < len(grid) else []
            lo = 0 if c1 is None else c1
            hi = len(row) - 1 if c2 is None else c2
            cells = []
            for idx, value in enumerate(row[lo : hi + 1], start=lo):
                cell = {"userEnteredValue": {"stringValue": str(value)}}
                if idx in self._owner.validated_columns.get(tab, ()):
                    cell["dataValidation"] = {"condition": {"type": "ONE_OF_LIST"}}
                cells.append(cell)
            return _Exec({"sheets": [{"data": [{"rowData": [{"values": cells}]}]}]})

        self._owner.calls.append(("structure", fields))
        if fields and "tables" in fields and self._owner.tables_unsupported:
            raise RuntimeError("API sem suporte a `tables`")
        sheets = []
        for tab, grid in self._owner.data.items():
            sheet = {
                "properties": {
                    "title": tab,
                    "sheetId": abs(hash(tab)) % 10000,
                    "gridProperties": {"rowCount": 1000, "columnCount": 26},
                }
            }
            if fields and "tables" in fields:
                table = self._owner.tables.get(tab)
                if table:
                    sheet["tables"] = [table]
            sheets.append(sheet)
        return _Exec({"sheets": sheets})

    def batchUpdate(self, *args, **kwargs):  # noqa: N802 - nome da API
        raise AssertionError(
            "batchUpdate e proibido pela politica do CRM e nao deve ser chamado."
        )


class FakeService:
    def __init__(self, spreadsheet_id: str, data: dict[str, list[list]]):
        self.spreadsheet_id = spreadsheet_id
        self.data = data
        self.calls: list[tuple[str, str]] = []
        self.tables: dict[str, dict] = {}
        self.validated_columns: dict[str, tuple[int, ...]] = {}
        self.tables_unsupported = False
        self._ss = FakeSpreadsheets(self)

    def assert_spreadsheet(self, got: str) -> None:
        assert got == self.spreadsheet_id, (
            f"gateway usou a planilha errada: {got!r} != {self.spreadsheet_id!r}"
        )

    def spreadsheets(self):
        return self._ss
