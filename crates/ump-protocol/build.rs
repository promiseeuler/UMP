fn main() {
    let protoc = protoc_bin_vendored::protoc_bin_path().expect("vendored protoc unavailable");
    let mut config = prost_build::Config::new();
    config.protoc_executable(protoc);
    config
        .compile_protos(&["../../schemas/ump/v1/core.proto"], &["../../schemas"])
        .expect("failed to compile UMP schemas");
    println!("cargo:rerun-if-changed=../../schemas/ump/v1/core.proto");
}
