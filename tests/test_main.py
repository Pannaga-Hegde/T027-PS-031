from ps031.main import main


def test_main_prints_initialization_message(capsys):
    main()

    output = capsys.readouterr().out

    assert "PS-031 Quick Commerce Engine" in output
    assert "Project initialized successfully." in output
