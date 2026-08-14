use std::{net::SocketAddr, sync::Arc};

use quinn::{
    ClientConfig as QuinnClientConfig, Endpoint, ServerConfig as QuinnServerConfig,
    crypto::rustls::{QuicClientConfig, QuicServerConfig},
};
use rustls::{ClientConfig, ServerConfig};
use thiserror::Error;

const ALPN_UMP_V1: &[u8] = b"ump/1";

#[derive(Debug, Error)]
pub enum QuicConfigError {
    #[error("QUIC TLS configuration failed: {0}")]
    Tls(#[from] rustls::Error),
    #[error("QUIC requires a TLS 1.3 cipher suite")]
    Cipher(#[from] quinn::crypto::rustls::NoInitialCipherSuite),
    #[error("endpoint I/O failed: {0}")]
    Io(#[from] std::io::Error),
}

pub fn server_endpoint(
    address: SocketAddr,
    config: Arc<ServerConfig>,
) -> Result<Endpoint, QuicConfigError> {
    let mut tls = (*config).clone();
    tls.alpn_protocols = vec![ALPN_UMP_V1.to_vec()];
    let crypto = QuicServerConfig::try_from(tls)?;
    let server = QuinnServerConfig::with_crypto(Arc::new(crypto));
    Ok(Endpoint::server(server, address)?)
}

pub fn client_endpoint(config: Arc<ClientConfig>) -> Result<Endpoint, QuicConfigError> {
    let mut tls = (*config).clone();
    tls.alpn_protocols = vec![ALPN_UMP_V1.to_vec()];
    let crypto = QuicClientConfig::try_from(tls)?;
    let mut endpoint = Endpoint::client((std::net::Ipv4Addr::LOCALHOST, 0).into())?;
    endpoint.set_default_client_config(QuinnClientConfig::new(Arc::new(crypto)));
    Ok(endpoint)
}

pub fn peer_certificate_fingerprint(connection: &quinn::Connection) -> Option<String> {
    let identity = connection.peer_identity()?;
    let certificates = identity
        .downcast::<Vec<rustls::pki_types::CertificateDer<'static>>>()
        .ok()?;
    certificates
        .first()
        .map(|certificate| crate::credentials::certificate_fingerprint(certificate.as_ref()))
}

#[cfg(test)]
mod tests {
    use ump_protocol::v1::envelope::Body;
    use ump_runtime::Machine;

    use super::*;
    use crate::{
        credentials::DevelopmentPki,
        framing::{read_envelope, write_envelope},
    };

    #[tokio::test]
    async fn mutually_authenticated_quic_exchanges_protobuf() {
        let pki = DevelopmentPki::generate().unwrap();
        let server_identity = pki.issue("arm-1.ump.local").unwrap();
        let client_identity = pki.issue("base-1.ump.local").unwrap();
        let server_config = pki.server_config(&server_identity).unwrap();
        let client_config = pki.client_config(&client_identity).unwrap();
        let expected_client_fingerprint = client_identity.fingerprint();
        let server_endpoint =
            server_endpoint((std::net::Ipv4Addr::LOCALHOST, 0).into(), server_config).unwrap();
        let address = server_endpoint.local_addr().unwrap();
        let client_endpoint = client_endpoint(client_config).unwrap();

        let server = tokio::spawn(async move {
            let incoming = server_endpoint.accept().await.unwrap();
            let connection = incoming.await.unwrap();
            assert_eq!(
                peer_certificate_fingerprint(&connection).as_deref(),
                Some(expected_client_fingerprint.as_str())
            );
            let (mut send, mut receive) = connection.accept_bi().await.unwrap();
            let request = read_envelope(&mut receive).await.unwrap();
            assert!(matches!(request.body, Some(Body::Hello(_))));
            write_envelope(&mut send, &request).await.unwrap();
            send.finish().unwrap();
            send.stopped().await.unwrap();
        });

        let connection = client_endpoint
            .connect(address, "arm-1.ump.local")
            .unwrap()
            .await
            .unwrap();
        let (mut send, mut receive) = connection.open_bi().await.unwrap();
        let mut machine = Machine::new("ump:machine:base-1", "session-base-1");
        let request = machine.hello(0);
        write_envelope(&mut send, &request).await.unwrap();
        let response = read_envelope(&mut receive).await.unwrap();
        assert_eq!(response.message_id, request.message_id);
        server.await.unwrap();
    }

    #[tokio::test]
    async fn quic_rejects_wrong_server_identity() {
        let pki = DevelopmentPki::generate().unwrap();
        let server_identity = pki.issue("arm-1.ump.local").unwrap();
        let client_identity = pki.issue("base-1.ump.local").unwrap();
        let server_config = pki.server_config(&server_identity).unwrap();
        let client_config = pki.client_config(&client_identity).unwrap();
        let server_endpoint =
            server_endpoint((std::net::Ipv4Addr::LOCALHOST, 0).into(), server_config).unwrap();
        let address = server_endpoint.local_addr().unwrap();
        let client_endpoint = client_endpoint(client_config).unwrap();

        let server = tokio::spawn(async move {
            let incoming = server_endpoint.accept().await.unwrap();
            incoming.await
        });
        let result = client_endpoint
            .connect(address, "wrong-machine.ump.local")
            .unwrap()
            .await;
        assert!(result.is_err());
        let _ = server.await;
    }

    #[tokio::test]
    async fn separately_enrolled_roots_bind_peer_fingerprint() {
        let server_pki = DevelopmentPki::generate().unwrap();
        let client_pki = DevelopmentPki::generate().unwrap();
        let server_identity = server_pki.issue("arm-1.ump.local").unwrap();
        let client_identity = client_pki.issue("base-1.ump.local").unwrap();
        let expected_client_fingerprint = client_identity.fingerprint();
        let server_config = crate::credentials::server_config_from_der_roots(
            vec![client_pki.root_certificate_der().to_vec()],
            server_identity.certificate_der().to_vec(),
            server_identity.private_key_bytes().to_vec(),
        )
        .unwrap();
        let client_config = crate::credentials::client_config_from_der(
            vec![server_pki.root_certificate_der().to_vec()],
            client_identity.certificate_der().to_vec(),
            client_identity.private_key_bytes().to_vec(),
        )
        .unwrap();
        let server_endpoint =
            server_endpoint((std::net::Ipv4Addr::LOCALHOST, 0).into(), server_config).unwrap();
        let address = server_endpoint.local_addr().unwrap();
        let client_endpoint = client_endpoint(client_config).unwrap();

        let server = tokio::spawn(async move {
            let connection = server_endpoint.accept().await.unwrap().await.unwrap();
            peer_certificate_fingerprint(&connection)
        });
        let _connection = client_endpoint
            .connect(address, "arm-1.ump.local")
            .unwrap()
            .await
            .unwrap();
        assert_eq!(server.await.unwrap(), Some(expected_client_fingerprint));
    }
}
