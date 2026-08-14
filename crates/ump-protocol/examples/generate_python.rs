use std::process::Command;

fn main() {
    let protoc = protoc_bin_vendored::protoc_bin_path().expect("vendored protoc unavailable");
    let status = Command::new(protoc)
        .args([
            "--proto_path=schemas",
            "--python_out=sdk/python",
            "schemas/ump/v1/core.proto",
            "schemas/ump/v1/machine.proto",
            "schemas/ump/v1/task.proto",
            "schemas/ump/v1/coordination.proto",
        ])
        .status()
        .expect("failed to execute vendored protoc");
    assert!(
        status.success(),
        "Python Protocol Buffers generation failed"
    );
}
