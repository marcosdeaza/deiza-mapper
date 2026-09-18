<!-- theme: paper -->
# Technical Specification

System integration standards and compliance criteria for partner platforms.

> [!TIP]
> Use mutual TLS authentication on port 8443 for all inter-service communication.

## Protocol Support

| Protocol | Version | Transport | Encryption |
|:---|:---|:---|:---|
| gRPC | 1.62 | HTTP/2 | TLS 1.3 |
| REST | 2.1 | HTTP/1.1 | TLS 1.3 |
| WebSocket | RFC 6455 | TCP | WSS |

The authentication token lifetime is determined by $T_{\text{session}} = \min(t_{\text{expiry}}, 3600)$.
