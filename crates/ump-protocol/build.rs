fn main() {
    let protoc = protoc_bin_vendored::protoc_bin_path().expect("vendored protoc unavailable");
    let mut config = prost_build::Config::new();
    config.protoc_executable(protoc);
    config
        .compile_protos(
            &[
                "../../schemas/ump/v1/core.proto",
                "../../schemas/ump/v1/machine.proto",
                "../../schemas/ump/v1/task.proto",
                "../../schemas/ump/v1/coordination.proto",
            ],
            &["../../schemas"],
        )
        .expect("failed to compile UMP schemas");
    println!("cargo:rerun-if-changed=../../schemas/ump/v1/core.proto");
    println!("cargo:rerun-if-changed=../../schemas/ump/v1/machine.proto");
    println!("cargo:rerun-if-changed=../../schemas/ump/v1/task.proto");
    println!("cargo:rerun-if-changed=../../schemas/ump/v1/coordination.proto");
}
