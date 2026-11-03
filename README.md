# Real Estate Price Tracking System (Hệ Thống Theo Dõi Biến Động Giá Bất Động Sản)

Hệ thống thu thập dữ liệu bất động sản phân tán, theo kiến trúc hướng sự kiện (Event-Driven) và Medallion Architecture (Bronze - Silver - Gold), tự động nhận diện tin trùng lặp, biến động giá, số điện thoại người bán, và tính toán chỉ số trung bình theo phường/xã.

---

## 1. Mục Đích & Điểm Nổi Bật

- **Event-Driven Streaming**: Sử dụng Apache Kafka làm trục xương sống, tách biệt hoàn toàn giữa spiders cào dữ liệu và các workers xử lý lưu trữ.
- **Medallion Data Lakehouse Architecture**:
  - **Bronze (MongoDB)**: Lưu trữ nguyên bản JSON/HTML raw payload, bảo đảm không mất mát dữ liệu.
  - **Silver (PostgreSQL + PostGIS)**: Dữ liệu chuẩn hoá bảng `listings`, `listing_details`, `advertisers`. Phân tích tọa độ địa lý, giải mã số điện thoại, dedup fingerprint.
  - **Gold (PostgreSQL & Materialized Views)**: Lịch sử biến động giá (`listing_price_history`), bảng tổng hợp giá $m^2$ theo phường/xã (`mv_ward_avg_price_per_m2`), tin giảm giá sâu trong 7 ngày (`mv_steep_price_drops_7d`).
- **Clean Hexagonal Architecture**: Tách biệt Domain, Application (Use Cases & Ports Protocol), Infrastructure và Entrypoints.
- **Per-Site YAML Configuration**: Quản lý cấu hình từng trang (`configs/sites/*.yaml`), dễ dàng mở rộng thêm sàn BĐS mới mà không cần sửa code.

---

## 2. Kiến Trúc Thư Mục

```
real-estate-tracker/
├── README.md                     # mục đích, kiến trúc, cách chạy 5 phút
├── LICENSE                       # MIT License
├── pyproject.toml                # deps, ruff, mypy, pytest gom một chỗ
├── uv.lock                       # khóa phiên bản
├── .python-version               # Python 3.13
├── .env.example                  # chỉ chứa biến mà Settings thực sự đọc
├── Makefile                      # make up / test / lint / migrate
│
├── src/realestate/
│   ├── domain/                   # thuần logic, không import hạ tầng
│   │   ├── entities/             # listing, advertiser, crawl_run, crawler_config
│   │   ├── value_objects/        # price, area, listing_key, fingerprint
│   │   ├── services/             # price_change, fingerprint
│   │   └── location/             # parser địa chỉ + dữ liệu hành chính 63 tỉnh thành
│   ├── application/
│   │   ├── ports/                # interface (Protocol): repo, publisher, spider
│   │   └── use_cases/            # crawl_site, ingest_silver_gold, store_bronze, apply_lifecycle
│   ├── infrastructure/
│   │   ├── crawler/
│   │   │   ├── spiders/          # generic_api_spider, generic_html_spider, spider_factory
│   │   │   └── parsing/          # price, area, phone, datetime, html_cleaner, road_width
│   │   ├── persistence/
│   │   │   ├── postgres/         # listing_repo, lifecycle_repo, config_repo, crawl_run_repo
│   │   │   └── mongo/            # bronze_repo, setup
│   │   ├── messaging/            # producer, base_consumer, clean_consumer, raw_consumer, dlq
│   │   └── config/               # Settings (pydantic-settings)
│   ├── entrypoints/              # composition roots
│   │   ├── scheduler.py          # Crawler Scheduler
│   │   ├── bronze_worker.py      # Raw Mongo Consumer
│   │   ├── silver_gold_worker.py # Relational Postgres Consumer
│   │   └── cli.py                # crawl, replay-dlq, reset
│   └── bootstrap.py              # dependency injection container
│
├── configs/sites/                # file YAML cấu hình từng site
│   ├── meeyland.yaml
│   ├── nhatot.yaml
│   └── ...
│
├── migrations/                   # Alembic versioned migrations
├── tests/
│   ├── unit/                     # domain & parsers
│   ├── contract/                 # kiểm tra tính hợp lệ của mọi YAML config
│   ├── integration/              # Postgres, Mongo, Kafka
│   └── fixtures/                 # HTML/JSON payloads mẫu
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── docs/
    ├── architecture.md           # sơ đồ luồng dữ liệu
    ├── adr/                      # Architecture Decision Records
    └── runbook.md                # vận hành, DLQ replay, lifecycle
```

---

## 3. Hướng Dẫn Chạy Trong 5 Phút (Quick Start)

### Bước 1: Chuẩn bị môi trường
```bash
cp .env.example .env
```

### Bước 2: Khởi động toàn bộ cụm dịch vụ
```bash
make up
# hoặc: docker compose -f docker/docker-compose.yml up -d --build
```
Dịch vụ sẽ khởi tạo:
- **Kafka & Zookeeper-less Raft**: Cổng `16100` (Kafka UI: `http://localhost:16101`)
- **MongoDB (Bronze)**: Cổng `16102`
- **PostgreSQL (Silver & Gold)**: Cổng `16103`
- **Crawler Scheduler & Workers**: Tự động kết nối và chạy ngầm.

### Bước 3: Chạy test kiểm thử toàn bộ hệ thống
```bash
pytest tests/
```

### Bước 4: Chạy cào dữ liệu thủ công qua CLI
```bash
# Cào tin tức thời (Incremental)
python -m realestate.entrypoints.cli crawl --site meeyland --mode incremental

# Cào quét toàn bộ danh mục (Full Sweep)
python -m realestate.entrypoints.cli crawl --site nhatot --mode full_sweep
```

### Bước 5: Replay Dead Letter Queue (DLQ)
```bash
python -m realestate.entrypoints.cli replay-dlq --site-id meeyland --limit 100
```

