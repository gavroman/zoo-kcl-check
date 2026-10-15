# Разработка и сборка

Пользовательская установка и примеры команд находятся в [README](../README.md).
Здесь — команды для изменения Python-кода, тестов и сборки.

## Зависимости

Python 3.11–3.14. Из корня репозитория:

```sh
python -m pip install --only-binary=:all: -r requirements.lock
python -m pip install --no-build-isolation --no-deps -e .
```

Точные версии зависимостей закреплены в `requirements.lock`.

## Проверки

```sh
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m build --no-isolation
```

Основные тесты запускают настоящий `zoo-kcl==0.3.191` через CLI. Фикстуры явно
задают KCL 3.0. Подмены используются для воспроизведения сбоев: зависания,
аварийного выхода, native panic и неполного отчёта.

На macOS отдельно проверяется работа без сети:

```sh
python -m pytest tests/test_offline.py
```

Тест сначала подтверждает запрет сетевых сокетов средствами macOS, затем запускает
четыре команды под тем же запретом, без Zoo-токенов. Подмена Python `socket`
не заменяет этот тест. Если внешняя песочница запрещает `sandbox_apply`, тест
пропускается с объяснением; запускайте его из обычного терминала.

## Сборка Python-пакета

```sh
python -m build --no-isolation
```

Результат: wheel и sdist в `dist/`.

При каждом выпуске обновляйте версию в `pyproject.toml` и `src/zoo_kcl_check_cli/__init__.py`.

## Сборка автономного executable

```sh
python -m PyInstaller --clean --noconfirm packaging/kcl-check.spec
./dist/kcl-check --help
python -m pytest tests/test_binary.py
```

Результат: `dist/kcl-check` на macOS/Linux или `dist/kcl-check.exe` на Windows.
Включает Python и нативную CAD-библиотеку. Собирать нужно на целевой ОС
и архитектуре.

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

Приёмка CLI проводилась на macOS ARM64 с Python 3.13. Готовые сборки
`zoo-kcl==0.3.191` доступны для Python 3.11–3.14; для Python 3.15 их нет.
На Windows и Linux полная приёмка CLI не проводилась.

## Источники API

- [Python bindings Zoo KCL](https://github.com/KittyCAD/modeling-app/blob/main/rust/kcl-python-bindings/src/lib.rs).
- [zoo-kcl 0.3.191](https://pypi.org/project/zoo-kcl/0.3.191/).
- [PyInstaller onefile](https://pyinstaller.org/en/stable/operating-mode.html).
- [PyInstaller multiprocessing](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing).
