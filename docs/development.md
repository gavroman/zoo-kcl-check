# Разработка и сборка

Пользовательская установка и примеры команд находятся в [README](../README.md).
Здесь — команды для изменения Python-кода, тестов и сборки.

## Окружение разработчика

Команды для macOS/Linux, из корня репозитория:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
```

`.venv` — отдельное Python-окружение этого проекта. `requirements.lock` содержит
точные версии всех зависимостей для разработки и сборки. `-e .` подключает
текущие исходники, поэтому изменения кода сразу доступны CLI.

Последняя команда использует уже установленные зависимости и инструменты сборки.
Её нужно выполнять после установки `requirements.lock`.

В Windows используйте `py -3.13 -m venv .venv` и замените `.venv/bin/python`
на `.venv\Scripts\python.exe`, а `.venv/bin/kcl-check` — на
`.venv\Scripts\kcl-check.exe`.

Альтернатива с обычным pip, без закрепления транзитивных версий:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --only-binary=zoo-kcl '.[dev]'
```

`.[dev]` означает «этот проект вместе с инструментами разработки». Установка
без `[dev]` ставит только CLI и его зависимость `zoo-kcl`:

```sh
.venv/bin/python -m pip install --only-binary=zoo-kcl .
.venv/bin/kcl-check --help
```

В этой команде `.` — текущая папка проекта. `--only-binary=zoo-kcl` требует
скачать готовый установочный архив CAD-библиотеки вместо компиляции её исходников.
Для платформы без такого архива установка завершится ошибкой.
Команда `kcl-check` при этом находится внутри `.venv`; для запуска используйте
путь `.venv/bin/kcl-check` или активируйте окружение.

## Проверки

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m build --no-isolation
```

Команды выше рассчитаны на окружение из `requirements.lock`. Если установили
только `.[dev]`, запускайте `.venv/bin/python -m build` без `--no-isolation`:
сборщик сам установит нужный Hatchling во временное окружение.

Основные тесты запускают настоящий `zoo-kcl==0.3.191` через CLI. Фикстуры явно
задают KCL 3.0. Подмены используются для воспроизведения сбоев: зависания,
аварийного выхода, native panic и неполного отчёта.

На macOS отдельно проверяется работа без сети:

```sh
.venv/bin/python -m pytest tests/test_offline.py
```

Тест сначала подтверждает запрет сетевых сокетов средствами macOS, затем запускает
четыре команды под тем же запретом, без Zoo-токенов. Подмена Python `socket`
не заменяет этот тест. Если внешняя песочница запрещает `sandbox_apply`, тест
пропускается с объяснением; запускайте его из обычного терминала.

## Сборка Python-пакета

```sh
.venv/bin/python -m build --no-isolation
```

В `dist/` появятся два файла:

- `.whl` (wheel) — готовый установочный архив Python-пакета;
- `.tar.gz` — архив исходников для сборки пакета.

Оба формата устанавливает pip. Файл `.whl` нашей обёртки требует Python и
библиотеку `zoo-kcl`; pip устанавливает зависимость автоматически.

Для установки собранного пакета:

```sh
.venv/bin/python -m pip install --only-binary=zoo-kcl dist/zoo_kcl_check_cli-0.1.0-py3-none-any.whl
```

При каждом выпуске обновляйте версию в `pyproject.toml` и `src/zoo_kcl_check_cli/__init__.py`.
Публикация исходников в GitHub и публикация пакета в PyPI — отдельные действия.

## Сборка автономного executable

При установке `requirements.lock` PyInstaller уже доступен:

```sh
.venv/bin/python -m PyInstaller --clean --noconfirm packaging/kcl-check.spec
./dist/kcl-check --help
.venv/bin/python -m pytest tests/test_binary.py
```

При обычной установке сначала добавьте инструменты сборки:

```sh
.venv/bin/python -m pip install --only-binary=zoo-kcl '.[dev,binary]'
```

Результат: `dist/kcl-check` на macOS/Linux или `dist/kcl-check.exe` на Windows.
Он включает Python и нативную CAD-библиотеку. Устанавливать Python получателю
не требуется. Собирать нужно на целевой ОС и архитектуре.

Тесты executable проверяют встроенный backend, worker, импорты и JSON из другой
папки, с очищенным Python-окружением. До сборки executable эти тесты пропускаются.

В управляемой песочнице Codex на macOS могут быть запрещены `semctl` и вложенный
`sandbox_apply`; запускайте приёмку из обычного терминала. Если запрещена запись
в стандартный кеш PyInstaller, задайте свой каталог через `PYINSTALLER_CONFIG_DIR`.

## Контракт результата

`--json` выдаёт один объект в stdout, в том числе при ошибках входа и таймаутах.
Основные поля: `schema_version`, `command`, `ok`, `exit_code`, `mode`, `backend`,
`geometry_validated`, `targets`, `diagnostics`, `sketches`, `formatting`.

`geometry_validated` всегда `false`. Неизвестная команда даёт `command: null` и
`mode: null`. Help и version остаются текстовыми. Режимы: `local_format`,
`local_lint`, `local_mock_execution`, `local_sketch_constraints`.

Диагностика хранит полный отчёт библиотеки в `rendered`. `file` и `range` могут быть
`null`: CLI не угадывает позиции ошибок импортированных модулей. У известных
диапазонов строки/колонки начинаются с 1, конец исключающий, колонки считаются
в Unicode-символах. Байтовые UTF-8 позиции линтера преобразуются в эти диапазоны.

Отчёт эскизов содержит `complete`, `total` и `entries`. У каждого элемента есть
`name`, `instance_index`, `status`, `free_count`, `conflict_count`, `total_count`.
Индекс начинается с 0. Счётчики описывают сегменты эскиза. Полный пустой отчёт
успешен; частичный отчёт сохраняется вместе с ошибкой и не считается успехом.

`formatting` содержит `file`, `changed`, `written`, `formatted_code`. Код включается
только при выводе форматирования без `--write`/`--check`. Форматирование всех файлов
готовится до начала записи: ошибка чтения или разбора отменяет запись набора.
Каждая замена файла атомарна, сохраняет права, неизменившиеся файлы не переписываются.
При ошибке записи или таймауте уже записанные файлы отмечаются `written: true`.

## Архитектура и зависимости

- `cli.py`: аргументы, help и вывод.
- `core.py`: пути, обход проектов и запись форматирования.
- `backend.py`: разрешённые вызовы Zoo KCL и нормализация результатов.
- `runner.py`: отдельный процесс с `spawn`, таймаут и завершение при прерывании.
- `models.py`: запросы, результаты и диагностика.

Нативная библиотека импортируется только в worker. До импорта удаляются
`ZOO_API_TOKEN` и `KITTYCAD_API_TOKEN`. CLI не читает credentials/config Zoo,
не вызывает облачное выполнение и не использует облачный fallback.

| Инструмент | Версия |
|---|---|
| `zoo-kcl` | `0.3.191` |
| `hatchling` | `1.32.4` |
| `build` | `1.6.1` |
| `pytest` | `9.1.1` |
| `ruff` | `0.17.0` |
| `PyInstaller` | `6.22.3` |

Проверены macOS 15.7.1 ARM64 и Python 3.13.7: 183 теста, включая 14 проверок с запретом
сети и 19 проверок executable. Для Python 3.11–3.14 опубликованы готовые архивы
`zoo-kcl` на macOS ARM64, Windows x64 и Linux glibc x64/ARM64. Полная приёмка CLI на
остальных платформах ещё не проводилась.

## Источники API

- [Python bindings Zoo KCL](https://github.com/KittyCAD/modeling-app/blob/main/rust/kcl-python-bindings/src/lib.rs).
- [zoo-kcl 0.3.191](https://pypi.org/project/zoo-kcl/0.3.191/).
- [PyInstaller onefile](https://pyinstaller.org/en/stable/operating-mode.html).
- [PyInstaller multiprocessing](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing).
