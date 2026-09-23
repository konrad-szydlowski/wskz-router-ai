"""docker-compose.yml guards: reproducible images, minimal exposed ports, readiness wiring."""

from pathlib import Path

import yaml

COMPOSE = yaml.safe_load((Path(__file__).parents[2] / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def test_every_image_is_pinned():
    for name, svc in SERVICES.items():
        if "image" in svc:
            repo, _, tag = svc["image"].rpartition(":")
            assert repo and tag and tag != "latest", f"{name}: {svc['image']}"


def test_only_api_and_mail_ui_are_published_on_localhost_with_overridable_ports():
    published = {name: svc.get("ports", []) for name, svc in SERVICES.items() if svc.get("ports")}
    assert set(published) == {"api", "mailpit"}  # Ollama 11434 and SMTP 1025 stay internal
    assert published["api"] == ["127.0.0.1:${API_PORT:-8000}:8000"]
    assert published["mailpit"] == ["127.0.0.1:${MAIL_UI_PORT:-8025}:8025"]


def test_no_fixed_container_names():
    assert not any("container_name" in svc for svc in SERVICES.values())


def test_api_waits_for_healthy_dependencies_and_has_readiness_healthcheck():
    api = SERVICES["api"]
    assert api["depends_on"]["ollama"]["condition"] == "service_healthy"
    assert api["depends_on"]["mailpit"]["condition"] == "service_healthy"
    assert "/api/v1/health" in " ".join(api["healthcheck"]["test"])
    assert all("healthcheck" in svc for svc in SERVICES.values())


def test_model_weights_live_in_a_named_volume():
    assert "ollama-models:/root/.ollama" in SERVICES["ollama"]["volumes"]
    assert "ollama-models" in COMPOSE["volumes"]
