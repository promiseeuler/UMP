use std::sync::Arc;

use rcgen::{
    BasicConstraints, CertificateParams, DnType, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
    KeyUsagePurpose,
};
use rustls::{
    ClientConfig, RootCertStore, ServerConfig,
    pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer},
    server::WebPkiClientVerifier,
};
use sha2::{Digest, Sha256};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum CredentialError {
    #[error("certificate generation failed: {0}")]
    Rcgen(#[from] rcgen::Error),
    #[error("TLS configuration failed: {0}")]
    Rustls(#[from] rustls::Error),
    #[error("client verifier configuration failed: {0}")]
    Verifier(#[from] rustls::server::VerifierBuilderError),
}

pub struct DevelopmentPki {
    issuer: Issuer<'static, KeyPair>,
    root_der: CertificateDer<'static>,
}

pub struct DevelopmentIdentity {
    pub dns_name: String,
    certificate: CertificateDer<'static>,
    private_key: Vec<u8>,
}

impl DevelopmentPki {
    pub fn generate() -> Result<Self, CredentialError> {
        let mut params = CertificateParams::new(Vec::<String>::new())?;
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params
            .distinguished_name
            .push(DnType::CommonName, "UMP Development Root");
        params.key_usages = vec![
            KeyUsagePurpose::DigitalSignature,
            KeyUsagePurpose::KeyCertSign,
            KeyUsagePurpose::CrlSign,
        ];
        let key = KeyPair::generate()?;
        let certificate = params.self_signed(&key)?;
        let root_der = certificate.der().clone();
        Ok(Self {
            issuer: Issuer::new(params, key),
            root_der,
        })
    }

    pub fn issue(
        &self,
        dns_name: impl Into<String>,
    ) -> Result<DevelopmentIdentity, CredentialError> {
        let dns_name = dns_name.into();
        let mut params = CertificateParams::new(vec![dns_name.clone()])?;
        params
            .distinguished_name
            .push(DnType::CommonName, dns_name.clone());
        params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
        params.extended_key_usages = vec![
            ExtendedKeyUsagePurpose::ServerAuth,
            ExtendedKeyUsagePurpose::ClientAuth,
        ];
        let key = KeyPair::generate()?;
        let certificate = params.signed_by(&key, &self.issuer)?;
        Ok(DevelopmentIdentity {
            dns_name,
            certificate: certificate.der().clone(),
            private_key: key.serialize_der(),
        })
    }

    pub fn client_config(
        &self,
        identity: &DevelopmentIdentity,
    ) -> Result<Arc<ClientConfig>, CredentialError> {
        let config = ClientConfig::builder()
            .with_root_certificates(self.root_store()?)
            .with_client_auth_cert(
                vec![identity.certificate.clone()],
                identity.private_key_der(),
            )?;
        Ok(Arc::new(config))
    }

    pub fn server_config(
        &self,
        identity: &DevelopmentIdentity,
    ) -> Result<Arc<ServerConfig>, CredentialError> {
        let verifier = WebPkiClientVerifier::builder(Arc::new(self.root_store()?)).build()?;
        let config = ServerConfig::builder()
            .with_client_cert_verifier(verifier)
            .with_single_cert(
                vec![identity.certificate.clone()],
                identity.private_key_der(),
            )?;
        Ok(Arc::new(config))
    }

    fn root_store(&self) -> Result<RootCertStore, CredentialError> {
        let mut roots = RootCertStore::empty();
        roots.add(self.root_der.clone())?;
        Ok(roots)
    }

    pub fn root_certificate_der(&self) -> &[u8] {
        self.root_der.as_ref()
    }
}

impl DevelopmentIdentity {
    fn private_key_der(&self) -> PrivateKeyDer<'static> {
        PrivatePkcs8KeyDer::from(self.private_key.clone()).into()
    }

    pub fn certificate_der(&self) -> &[u8] {
        self.certificate.as_ref()
    }

    pub fn private_key_bytes(&self) -> &[u8] {
        &self.private_key
    }

    pub fn fingerprint(&self) -> String {
        certificate_fingerprint(self.certificate_der())
    }
}

pub fn certificate_fingerprint(certificate_der: &[u8]) -> String {
    hex::encode(Sha256::digest(certificate_der))
}

pub fn server_config_from_der(
    root_der: Vec<u8>,
    certificate_der: Vec<u8>,
    private_key_der: Vec<u8>,
) -> Result<Arc<ServerConfig>, CredentialError> {
    server_config_from_der_roots(vec![root_der], certificate_der, private_key_der)
}

pub fn client_config_from_der(
    root_ders: Vec<Vec<u8>>,
    certificate_der: Vec<u8>,
    private_key_der: Vec<u8>,
) -> Result<Arc<ClientConfig>, CredentialError> {
    let mut roots = RootCertStore::empty();
    for root_der in root_ders {
        roots.add(CertificateDer::from(root_der))?;
    }
    let config = ClientConfig::builder()
        .with_root_certificates(roots)
        .with_client_auth_cert(
            vec![CertificateDer::from(certificate_der)],
            PrivatePkcs8KeyDer::from(private_key_der).into(),
        )?;
    Ok(Arc::new(config))
}

pub fn server_config_from_der_roots(
    root_ders: Vec<Vec<u8>>,
    certificate_der: Vec<u8>,
    private_key_der: Vec<u8>,
) -> Result<Arc<ServerConfig>, CredentialError> {
    let mut roots = RootCertStore::empty();
    for root_der in root_ders {
        roots.add(CertificateDer::from(root_der))?;
    }
    let verifier = WebPkiClientVerifier::builder(Arc::new(roots)).build()?;
    let config = ServerConfig::builder()
        .with_client_cert_verifier(verifier)
        .with_single_cert(
            vec![CertificateDer::from(certificate_der)],
            PrivatePkcs8KeyDer::from(private_key_der).into(),
        )?;
    Ok(Arc::new(config))
}
