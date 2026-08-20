use prost::Message;
use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use ump_protocol::v1::Envelope;

use crate::MAX_CONTROL_FRAME_BYTES;

#[derive(Debug, Error)]
pub enum FrameError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("control frame of {actual} bytes exceeds {maximum}-byte limit")]
    TooLarge { actual: usize, maximum: usize },
    #[error("invalid Protocol Buffers envelope: {0}")]
    Decode(#[from] prost::DecodeError),
}

pub async fn write_envelope<W>(writer: &mut W, envelope: &Envelope) -> Result<(), FrameError>
where
    W: AsyncWrite + Unpin,
{
    let encoded_len = envelope.encoded_len();
    if encoded_len > MAX_CONTROL_FRAME_BYTES {
        return Err(FrameError::TooLarge {
            actual: encoded_len,
            maximum: MAX_CONTROL_FRAME_BYTES,
        });
    }

    writer.write_u32(encoded_len as u32).await?;
    let mut encoded = Vec::with_capacity(encoded_len);
    envelope
        .encode(&mut encoded)
        .expect("pre-sized Vec cannot fail");
    writer.write_all(&encoded).await?;
    writer.flush().await?;
    Ok(())
}

pub async fn read_envelope<R>(reader: &mut R) -> Result<Envelope, FrameError>
where
    R: AsyncRead + Unpin,
{
    let encoded_len = reader.read_u32().await? as usize;
    if encoded_len > MAX_CONTROL_FRAME_BYTES {
        return Err(FrameError::TooLarge {
            actual: encoded_len,
            maximum: MAX_CONTROL_FRAME_BYTES,
        });
    }

    let mut encoded = vec![0; encoded_len];
    reader.read_exact(&mut encoded).await?;
    Ok(Envelope::decode(encoded.as_slice())?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    #[tokio::test]
    async fn rejects_oversized_frame_before_allocating_body() {
        let (mut writer, mut reader) = duplex(16);
        writer
            .write_u32((MAX_CONTROL_FRAME_BYTES + 1) as u32)
            .await
            .unwrap();

        let error = read_envelope(&mut reader).await.unwrap_err();
        assert!(matches!(error, FrameError::TooLarge { .. }));
    }
}
