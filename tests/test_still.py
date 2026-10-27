from nitm import still


def test_measure_runs_the_command_it_prints():
    ran = []

    def runner(command):
        ran.append(command)
        return "82\n"

    m = still.measure("rdepends", "zlib1g", run=runner)
    assert m.value == 82
    assert ran == [m.command]
    assert m.command.startswith("apt-cache rdepends --installed")
    assert m.sentence == "82 installed packages on this machine depend on zlib1g."
    assert m.measured_at[:2] == "20"


def test_a_failed_probe_is_not_measured_never_guessed():
    def runner(command):
        raise RuntimeError("apt-cache: not found")

    m = still.measure("rdepends", "zlib1g", run=runner)
    assert m.value is None and m.sentence == "" and "not found" in m.error


def test_probe_commands():
    assert "dpkg.log" in still.dpkg_log_command()
    assert "import" in still.importers_command("xmlrpc", "/usr/lib/python3.13")
    assert "METADATA" in still.dist_count_command("html2text", ["/usr/lib/python3"])


def test_singular_sentences():
    assert still.measure("rdepends", "libfoo", run=lambda c: "1").sentence == "1 installed package on this machine depends on libfoo."
    assert still.measure("dist-count", "html2text", run=lambda c: "1").sentence == "1 Python environment read by this scan has html2text installed."
