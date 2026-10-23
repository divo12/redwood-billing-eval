# The grader: the task image plus the hidden verifier, root-only. Graded agents run as `nobody` in this container,
# so nothing hidden may be mounted or world-readable here (scripts/grade_snapshots.sh builds and runs it).
FROM redwood-grade
COPY tests /grader/tests
RUN chmod -R go-rwx /grader && chmod 711 /grader
WORKDIR /grader/tests
