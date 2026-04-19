from app.models.achievement import Achievement

MEDALS = {
    # Be the only FC in a multiplayer lobby that has at least 4 players.
    Achievement(
        id=353,
        name="Hotshot",
        desc="Shine brighter than anyone else, in a way only you can.",
        assets_id="all-secret-hotshot",
        clientside=True,
    ): None,
    # Reach 477 spins per minute on a spinner.
    Achievement(
        id=354,
        name="Cyclone",
        desc="Clockwise or anticlockwise, that is the question.",
        assets_id="osu-skill-cyclone",
        clientside=True,
    ): None,
    # Spin your cursor around on any menu while holding left click until your arm falls off.
    Achievement(
        id=355,
        name="Hamster Wheel",
        desc="Feeling dizzy yet?",
        assets_id="all-secret-hamsterwheel",
        clientside=True,
    ): None,
    # Drag and "throw" any notification to the left.
    Achievement(
        id=356,
        name="Courier Catapult",
        desc="YEET!",
        assets_id="all-secret-couriercatapult",
        clientside=True,
    ): None,
}
