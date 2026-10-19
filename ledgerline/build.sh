#!/usr/bin/env bash
# Offline build for Ledgerline: compile, run JUnit 5, package target/ledgerline.war.
#
# Uses jars already on the machine (Debian/Ubuntu: tomcat10, libpostgresql-jdbc-java,
# libjackson2-databind-java, junit5). On a machine with internet, `mvn verify` does the
# same thing from pom.xml; CI uses Maven.
#
#   JAR_DIR   where the jars live            (default /usr/share/java)
#   SKIP_TESTS=1 to package without testing
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
jar_dir="${JAR_DIR:-/usr/share/java}"
out="$here/target"

need() {
  local f="$jar_dir/$1"
  [[ -f "$f" ]] || { echo "build.sh: missing $f (install the package that provides it)" >&2; exit 2; }
  printf '%s' "$f"
}

servlet="$(need tomcat10-servlet-api.jar)"
pool="$(need tomcat10-jdbc.jar)"
juli="$(need tomcat10-juli.jar)"
pg="$(need postgresql.jar)"
jcore="$(need jackson-core.jar)"
jbind="$(need jackson-databind.jar)"
jann="$(need jackson-annotations.jar)"
junit="$(need junit-platform-console-standalone.jar)"

compile_cp="$servlet:$pool:$juli:$pg:$jcore:$jbind:$jann"

rm -rf "$out"
mkdir -p "$out/classes" "$out/test-classes" "$out/war/WEB-INF/classes" "$out/war/WEB-INF/lib"

echo "==> compiling main"
find "$here/src/main/java" -name '*.java' > "$out/main-sources.txt"
javac --release 21 -Xlint:all,-serial,-processing -Werror -d "$out/classes" -cp "$compile_cp" @"$out/main-sources.txt"

if [[ "${SKIP_TESTS:-0}" != "1" ]]; then
  echo "==> compiling tests"
  find "$here/src/test/java" -name '*.java' > "$out/test-sources.txt"
  javac --release 21 -d "$out/test-classes" -cp "$out/classes:$compile_cp:$junit" @"$out/test-sources.txt"
  echo "==> running tests"
  java -jar "$junit" execute --disable-banner --details=summary \
    --class-path "$out/classes:$out/test-classes:$compile_cp" --scan-class-path "$out/test-classes" \
    --reports-dir "$out/test-reports"
fi

echo "==> packaging war"
cp -r "$out/classes/." "$out/war/WEB-INF/classes/"
cp "$here/src/main/webapp/WEB-INF/web.xml" "$out/war/WEB-INF/"
# runtime libraries the app brings itself; servlet API and the JDBC pool come from Tomcat
cp "$pg" "$jcore" "$jbind" "$jann" "$out/war/WEB-INF/lib/"
(cd "$out/war" && jar --create --file "$out/ledgerline.war" .)
echo "==> built $out/ledgerline.war"
