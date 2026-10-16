ARG CRDB_VERSION=v26.2.8
FROM cockroachdb/cockroach:${CRDB_VERSION}
COPY handler.sh /usr/local/bin/handler.sh
RUN chmod +x /usr/local/bin/handler.sh
ENTRYPOINT ["/usr/local/bin/handler.sh"]
CMD ["loop"]
