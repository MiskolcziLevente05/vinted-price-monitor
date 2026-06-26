# Vinted Smart Price Monitor 🐍 🛍️

A lightweight, standalone Python automation script designed to monitor specific search queries on Vinted in real-time. The script analyzes listings, filters out duplicates using a local database, and dispatches instant rich alerts via Discord Webhooks when an item is found.

Built to demonstrate **Python-based web automation, resilient scraping practices, and efficient scripting architecture** without depending on a heavy web framework.

---

## 🛠️ Tech Stack & Architecture

* **Language:** Python 3.x
* **Automation & Scraping:** `Selenium` + `selenium-extra-stealth` (Configured to bypass Cloudflare and automated bot detection)
* **Data Parsing:** `BeautifulSoup4` for rapid HTML tree extraction once the DOM is loaded
* **Database:** `SQLite` (A self-contained, zero-configuration SQL database engine to track seen items)
* **Notifications:** `Requests` library to communicate with Discord Webhook API

---

## 🚀 Key Features

* **Anti-Bot Resiliency:** Implements randomized delay intervals (*jitter*) and mimics human mouse/window behaviors using `selenium-stealth` to prevent IP rate-limiting.
* **Smart Filtering:** Uses an SQLite database backend to ensure users only receive notifications for *newly discovered* listings, avoiding spam.
* **Modular Codebase:** Organized into clean, reusable functions (`init_driver`, `parse_listings`, `check_db`, `send_alert`) adhering to clean code standards.
* **Zero-Sallang Architecture:** Built entirely as a background execution utility (CLI script)—no unnecessary UI or bloated server setups.

---

## 📋 Prerequisites & Installation

1. Clone the repository:
   ```bash
   git clone [https://github.com/MiskolcziLevente05/vinted-smart-analyst.git](https://github.com/MiskolcziLevente05/vinted-smart-analyst.git)
   cd vinted-smart-analyst
