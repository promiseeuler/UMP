#[tokio::main]
async fn main() -> anyhow::Result<()> {
    ump_cli::run_cli().await
}
