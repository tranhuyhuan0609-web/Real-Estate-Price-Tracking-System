# Real Estate Price Tracking System (Hệ Thống Theo Dõi Biến Động Giá Bất Động Sản)

Hệ thống thu thập dữ liệu bất động sản phân tán, theo kiến trúc hướng sự kiện (Event-Driven) và Medallion Architecture (Bronze → Silver → Gold): tự động nhận diện tin trùng lặp, biến động giá, số điện thoại người bán, và tính chỉ số giá trung bình theo phường/xã.

- **Kafka** tách spider (producer) khỏi các worker lưu trữ (consumer).
- **Bronze – MongoDB**: payload gốc (`raw_listings`) + kho DLQ (`dlq_events`).
- **Silver – PostgreSQL/PostGIS**: `listings`, `listing_details`, `advertisers` đã chuẩn hoá. Mỗi tin có `property_fingerprint` (hash tiêu đề + diện tích + địa bàn) để phân tích tin trùng giữa các sàn. Hệ thống không tự gộp tin.
- **Gold – PostgreSQL**: `listing_price_history` + materialized views `mv_ward_avg_price_per_m2`, `mv_steep_price_drops_7d`.
- **Hexagonal architecture**: `domain` ← `application` ← `infrastructure` ← `entrypoints` (được kiểm tra bằng `tests/contract/test_layering.py`).
- **Cấu hình site khai báo**: mỗi sàn là một file `configs/sites/<site_id>.yaml` — thêm sàn mới không cần sửa code.

Chi tiết: [docs/architecture.md](docs/architecture.md) · Vận hành: [docs/runbook.md](docs/runbook.md) · Quyết định kiến trúc: [docs/adr/](docs/adr/)

---

## Chạy trong 5 phút

Yêu cầu: Docker Compose v2, [uv](https://docs.astral.sh/uv/) (tự cài Python 3.13 theo `.python-version`), `make`.

```bash
cp .env.example .env          # đổi mật khẩu nếu cần
make up                       # Kafka, Mongo, Postgres+PostGIS, sync config, scheduler, 3 loại worker
make ps                       # kiểm tra container
```

`make up` chạy theo thứ tự: Postgres khởi tạo schema từ `sql/init.sql` (chỉ khi volume trống) → job `migrate` nâng DB lên migration mới nhất → job `config-sync` nạp `configs/sites/*.yaml` vào `crawler_configs` → scheduler và worker chạy (có healthcheck). `make up-monitoring` thêm Prometheus (http://localhost:16104) và Alertmanager gửi cảnh báo về **Telegram** (cài đặt: runbook mục 6). Backup Postgres/Mongo chạy hằng ngày vào `./backups` (runbook mục 9).

| Dịch vụ | Cổng host |
|---|---|
| Kafka | `16100` (UI: http://localhost:16101) |
| MongoDB (Bronze) | `MONGO_PORT`, mặc định `16102` |
| PostgreSQL (Silver/Gold) | `POSTGRES_PORT`, mặc định `16103` |

### Phát triển local

```bash
make install                  # tạo .venv từ uv.lock
make check                    # ruff + mypy + unit/contract test (giống CI)
make test-integration         # Postgres/Mongo thật qua testcontainers (cần Docker)

uv run realestate sync-configs --dry-run
uv run realestate crawl --site meeyland --mode incremental
uv run realestate replay-dlq --site-id meeyland --limit 100
```

`make help` liệt kê mọi lệnh.

---

## Cấu trúc thư mục

```
├── pyproject.toml            # dependencies, scripts, pytest, ruff, mypy
├── uv.lock                   # lock cho dev/CI (uv)
├── requirements.lock         # export từ uv.lock (có hash) cho Docker image — `make lock`
├── .python-version           # 3.13
├── .env.example              # đúng các biến Settings đọc (có contract test)
├── Makefile
├── alembic.ini
├── sql/init.sql              # SINH TỰ ĐỘNG từ migrations (`make schema`), Postgres chạy khi volume trống
├── configs/sites/*.yaml      # nguồn sự thật cho crawler_configs (ADR 0004)
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── monitoring/           # prometheus.yml, alerts.yml
├── docs/
│   ├── architecture.md
│   ├── runbook.md
│   └── adr/
├── migrations/               # Alembic — nguồn schema duy nhất (ADR 0006)
├── scripts/                  # generate_init_sql.py, capture_site_fixture.py (fixture HTML ẩn danh)
├── src/realestate/
│   ├── domain/               # entities, value_objects, services, location — không I/O
│   ├── application/
│   │   ├── ports/            # Protocol: repositories, publisher, spider
│   │   └── use_cases/        # crawl_site, store_bronze, ingest_silver_gold,
│   │                         # apply_lifecycle, reveal_phone, sync_site_configs
│   ├── infrastructure/
│   │   ├── crawler/          # spiders/, parsing/ (1 module / loại dữ liệu), http_clients (pool theo proxy)
│   │   ├── persistence/      # postgres/, mongo/
│   │   ├── messaging/        # producer, base_consumer + 3 consumer, DLQ (forward/replay)
│   │   └── config/           # Settings, loader cho configs/sites
│   ├── entrypoints/          # scheduler, bronze_worker, silver_gold_worker, dlq_worker, cli
│   └── bootstrap.py          # composition root (AppContainer, khởi tạo lazy)
└── tests/
    ├── unit/                 # domain/, parsers/, use_cases/, repos/, consumers/, config/
    ├── contract/             # YAML site, .env.example, schema ↔ migration, phân lớp
    ├── integration/          # PostGIS + Mongo thật (marker `integration`)
    ├── fixtures/             # payload JSON + trang HTML thật đã thu gọn và ẩn danh
    └── helpers.py            # test double dùng chung
```

## Thêm một sàn mới

Nếu feed mới crawl cùng sàn với một feed đã có (cùng không gian ID tin), khai báo `portal: <site_id gốc>` để tin trùng giữa hai feed được gộp (ADR 0005).

1. Tạo `configs/sites/<site_id>.yaml` (tên file phải trùng `site_id`).
2. `make test` — contract test kiểm tra schema, ngân sách trang, rate limit và round-trip qua DB.
3. `make sync-configs` (hoặc `make up`, job `config-sync` tự chạy).

## Git blame

Commit chỉ format code được liệt kê trong `.git-blame-ignore-revs`. Bật một lần ở máy local: `git config blame.ignoreRevsFile .git-blame-ignore-revs`.
