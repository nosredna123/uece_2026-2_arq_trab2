import json
from urllib.request import urlopen


ENDPOINTS = (
    "http://event-management:8080/actuator/health",
    "http://registration:8080/actuator/health",
    "http://attendance:8080/health",
    "http://reporting:8080/health",
    "http://identity-mock:8080/health",
)


def main() -> None:
    for endpoint in ENDPOINTS:
        with urlopen(endpoint, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"Health probe failed for {endpoint}")
            payload = json.load(response)
        if payload.get("status") != "UP":
            raise RuntimeError(f"Health probe failed for {endpoint}")
        print(f"healthy: {endpoint}")


if __name__ == "__main__":
    main()
