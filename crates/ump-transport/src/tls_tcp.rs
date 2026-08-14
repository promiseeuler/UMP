use std::{net::SocketAddr, sync::Arc};

use rustls::{ClientConfig, ServerConfig, pki_types::ServerName};
use thiserror::Error;
use tokio::net::{TcpListener, TcpStream};
use tokio_rustls::{TlsAcceptor, TlsConnector, client::TlsStream};

#[derive(Debug, Error)]
pub enum ConnectError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("TLS server name is invalid")]
    InvalidServerName,
}

pub async fn bind_loopback() -> Result<TcpListener, std::io::Error> {
    TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0)).await
}

pub async fn connect(
    address: SocketAddr,
    expected_server_name: &str,
    config: Arc<ClientConfig>,
) -> Result<TlsStream<TcpStream>, ConnectError> {
    let stream = TcpStream::connect(address).await?;
    stream.set_nodelay(true)?;
    let server_name = ServerName::try_from(expected_server_name.to_owned())
        .map_err(|_| ConnectError::InvalidServerName)?;
    Ok(TlsConnector::from(config)
        .connect(server_name, stream)
        .await?)
}

pub async fn accept(
    listener: &TcpListener,
    config: Arc<ServerConfig>,
) -> Result<tokio_rustls::server::TlsStream<TcpStream>, std::io::Error> {
    let (stream, _) = listener.accept().await?;
    stream.set_nodelay(true)?;
    TlsAcceptor::from(config).accept(stream).await
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use ump_protocol::v1::envelope::Body;
    use ump_runtime::Machine;

    use super::*;
    use crate::{
        credentials::DevelopmentPki,
        framing::{read_envelope, write_envelope},
    };

    #[tokio::test]
    async fn mutually_authenticated_peers_exchange_protobuf() {
        let pki = DevelopmentPki::generate().unwrap();
        let server_identity = pki.issue("arm-1.ump.local").unwrap();
        let client_identity = pki.issue("base-1.ump.local").unwrap();
        let server_config = pki.server_config(&server_identity).unwrap();
        let client_config = pki.client_config(&client_identity).unwrap();
        let listener = bind_loopback().await.unwrap();
        let address = listener.local_addr().unwrap();

        let server = tokio::spawn(async move {
            let mut stream = accept(&listener, server_config).await.unwrap();
            let request = read_envelope(&mut stream).await.unwrap();
            assert!(matches!(request.body, Some(Body::Hello(_))));
            write_envelope(&mut stream, &request).await.unwrap();
        });

        let mut stream = connect(address, "arm-1.ump.local", client_config)
            .await
            .unwrap();
        let mut machine = Machine::new("ump:machine:base-1", "session-base-1");
        let request = machine.hello(0);
        write_envelope(&mut stream, &request).await.unwrap();
        let response = read_envelope(&mut stream).await.unwrap();
        assert_eq!(response.message_id, request.message_id);
        tokio::time::timeout(Duration::from_secs(2), server)
            .await
            .unwrap()
            .unwrap();
    }

    #[tokio::test]
    async fn rejects_server_identity_mismatch() {
        let pki = DevelopmentPki::generate().unwrap();
        let server_identity = pki.issue("arm-1.ump.local").unwrap();
        let client_identity = pki.issue("base-1.ump.local").unwrap();
        let server_config = pki.server_config(&server_identity).unwrap();
        let client_config = pki.client_config(&client_identity).unwrap();
        let listener = bind_loopback().await.unwrap();
        let address = listener.local_addr().unwrap();

        let server = tokio::spawn(async move { accept(&listener, server_config).await });
        let result = connect(address, "wrong-machine.ump.local", client_config).await;
        assert!(result.is_err());
        let _ = server.await;
    }

    #[tokio::test]
    async fn rejects_client_from_untrusted_root() {
        let trusted_pki = DevelopmentPki::generate().unwrap();
        let untrusted_pki = DevelopmentPki::generate().unwrap();
        let server_identity = trusted_pki.issue("arm-1.ump.local").unwrap();
        let client_identity = untrusted_pki.issue("intruder.ump.local").unwrap();
        let server_config = trusted_pki.server_config(&server_identity).unwrap();
        let client_config = untrusted_pki.client_config(&client_identity).unwrap();
        let listener = bind_loopback().await.unwrap();
        let address = listener.local_addr().unwrap();

        let server = tokio::spawn(async move { accept(&listener, server_config).await });
        let client = connect(address, "arm-1.ump.local", client_config).await;
        let server = server.await.unwrap();
        assert!(client.is_err() || server.is_err());
    }
}
