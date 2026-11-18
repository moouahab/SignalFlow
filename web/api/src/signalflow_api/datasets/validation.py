"""Validation tabulaire en flux, compatible avec les contraintes du moteur ML."""
import csv
import io
import math

from ..errors import ApiError


def validate(stream, target):
    text = io.TextIOWrapper(stream, encoding='utf-8-sig', newline='')
    try:
        reader = csv.reader(text, strict=True)
        columns = next(reader)
        if (not 2 <= len(columns) <= 256 or len(set(columns)) != len(columns)
                or any(not c.strip() or len(c) > 128 or any(ord(x) < 32 for x in c) for c in columns)
                or target not in columns):
            raise ValueError()
        index = columns.index(target)
        present = set()
        classes = set()
        count = 0
        for row in reader:
            count += 1
            if count * len(columns) > 1_000_000:
                raise ValueError()
            if len(row) != len(columns) or not row[index].strip():
                raise ValueError()
            if any(len(v) > 4096 or any(ord(x) < 32 for x in v) for v in row):
                raise ValueError()
            if len(classes) < 2:
                classes.add(row[index])
            for i, value in enumerate(row):
                if i != index and value.strip():
                    number = float(value)
                    if math.isinf(number):
                        raise ValueError()
                    if not math.isnan(number):
                        present.add(i)
        if count < 3 or len(classes) < 2 or len(present) != len(columns) - 1:
            raise ValueError()
        return columns, count
    except (ValueError, UnicodeError, csv.Error, StopIteration):
        raise ApiError(422, 'INVALID_DATASET', 'CSV UTF-8 invalide ou données incompatibles.') from None
    finally:
        text.detach()
        stream.seek(0)
