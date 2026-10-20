# OPEN-ATLAS matching engine — multi-stage build.
# Builder compiles the whole crate; the runtime image carries only the
# fix_gateway binary and runs as a non-root user.
FROM rust:1.78-bookworm AS builder
WORKDIR /build
COPY . .
RUN cargo build --release --bin fix_gateway

FROM debian:bookworm-slim
RUN useradd -r -s /usr/sbin/nologin gateway
COPY --from=builder /build/target/release/fix_gateway /usr/local/bin/fix_gateway
USER gateway
EXPOSE 9101
CMD ["fix_gateway"]
