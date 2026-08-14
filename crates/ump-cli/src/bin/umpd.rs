use std::path::PathBuf;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let data_dir = std::env::var_os("UMP_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".ump"));
    ump_cli::run_daemon(&data_dir, false).await
}
