# zoo-kcl-check-cli

Локальные проверки [Zoo / KittyCAD KCL](https://zoo.dev/): форматирование,
синтаксис, стиль, ошибки выполнения и ограничения эскизов.
Работает без сети, Zoo-аккаунта и API-токенов.

Выполнение использует mock CAD-движок. **Успешная проверка не подтверждает
корректность 3D-геометрии** — её нужно проверять в Zoo.

## Установка

```sh
pipx install zoo-kcl-check-cli
```

Также доступны `pip install zoo-kcl-check-cli` и `uv tool install zoo-kcl-check-cli`.
Требуется Python 3.11–3.14. [Пакет в PyPI](https://pypi.org/project/zoo-kcl-check-cli/).

## Использование

```sh
kcl-check lint main.kcl
kcl-check execute main.kcl
kcl-check sketches main.kcl
kcl-check format main.kcl --write
```

| Команда | Назначение |
|---|---|
| `lint` | Проверка синтаксиса и стиля без выполнения |
| `execute` | Локальное выполнение: имена, аргументы, типы, единицы измерения и `assert` |
| `sketches` | Проверка определённости и конфликтов ограничений эскизов |
| `format` | Форматирование: вывод кода по умолчанию, запись с `--write`, проверка с `--check` |

Вместо файла можно передать папку:

```sh
kcl-check lint .
kcl-check execute .
kcl-check sketches .
kcl-check format . --check
```

`lint` и `format` обрабатывают `.kcl` рекурсивно, пропуская символические ссылки.
`execute` и `sketches` запускают `default_file` из `project.toml` или `main.kcl`.
Импорты соседних KCL-файлов поддерживаются.

Предупреждения тоже считаются неуспехом. Команда `sketches` завершается
неуспешно при недоопределённых или конфликтующих эскизах и неполном отчёте.

Параметры каждой команды доступны через `kcl-check COMMAND --help`.

## Вывод

По умолчанию вывод текстовый. Диагностика идёт в stderr, результаты и отчёты —
в stdout. Ошибки движка включают фрагмент кода и указатель на проблемное место,
если эта информация доступна.

Для скриптов, CI и coding-агентов:

```sh
kcl-check execute main.kcl --json
```

`--json` возвращает один объект в stdout, в том числе при ошибках.
Основные поля: `ok`, `exit_code`, `diagnostics`, `sketches`, `formatting`.
Полный формат описан в [контракте результата](docs/development.md#контракт-результата).

Все команды поддерживают `--timeout SECONDS`; по умолчанию — 60 секунд на всю команду.

| Код завершения | Значение |
|---|---|
| `0` | Проверка прошла |
| `1` | Диагностика KCL, проблема ограничений или отличие формата при `--check` |
| `2` | Некорректный ввод, таймаут или сбой инструмента |
| `130` | Прерывание пользователем |

## Скиллы для агентов

- [kcl-modeling](skills/kcl-modeling/SKILL.md) — правила моделирования и приёмы работы с KCL.
- [kcl-check](skills/kcl-check/SKILL.md) — установка CLI, запуск проверок и разбор результатов.

```sh
npx skills add gavroman/zoo-kcl-check --skill kcl-modeling --skill kcl-check
```

## Разработка

[Окружение, тесты и сборка](docs/development.md).
Ошибки и предложения — в [GitHub Issues](https://github.com/gavroman/zoo-kcl-check/issues).

## Лицензия

[MIT](LICENSE).
