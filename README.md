# Nebula Panel

**Nebula Panel** is a next-generation distributed infrastructure management dashboard. Currently, this project serves as a **technical preview and architectural concept**, demonstrating the communication between the Nebula Core and the Flask-based web interface.

> [!IMPORTANT]
> **Project Status:** Under Active Development. Core features such as Docker container orchestration and the multi-user system are currently in the laboratory stage and not yet functional.

## 🌌 The Vision

Nebula aims to be a robust, high-performance management solution primarily designed for **Ubuntu Server** environments. It will provide a seamless interface for distributing container workloads across multiple servers, managed through a secure, encrypted API.

## 🛠 Features in Technical Preview

While many management features are in development, the current build demonstrates:

* **Asynchronous Core Runtime:** A high-performance kernel capable of managing internal services.
* **Real-time Metrics:** Live data streaming from the Core to the Panel (CPU, RAM, Disk).
* **WebSocket Log Streaming:** Real-time log broadcasting from the system kernel to the browser console.
* **Auto-discovery:** The Panel automatically detects the Core on local ports (8000, 8080, or 5000).

## 🚀 Getting Started

To test the communication between the Core and the Panel, follow these steps:

### 1. Prerequisites

Ensure you have Python 3.9+ installed and install all required dependencies:

```bash
pip install -r requirements.txt

```

### 2. Launch the Nebula Core

The Core acts as the "brain" of the system.

* **On Windows:**
```bash
python startcore.py

```



### 3. Launch the Nebula Panel (GUI)

Open a new terminal window and run the Flask application:

```bash
cd ./nebula_gui_flask/
python app.py

```

### 4. Access the Dashboard

Once both services are running, open your browser and navigate to:
`http://127.0.0.1:5000`

---

## 🗺 Roadmap

* [ ] **User Management System:** Fine-grained permissions (ACL), hardware security, and end-to-end encryption.
* [ ] **Docker Integration:** Full container lifecycle management (Create, Stop, Inspect, Remove).
* [ ] **Resource Distribution:** Intelligent container placement across multiple server nodes.
* [ ] **Account API:** Public API for third-party integrations and automation.
* [ ] **Native Ubuntu Integration:** Deep system optimization for Linux production environments.

---

*Nebula Panel is an open-concept project for educational and experimental purposes. v1.0.0-pre-alpha • 2026*
