# Turtle Pokédex Advance

A Pokémon battle and Pokédex app inspired by the GBA. Developed using Turtle Graphics and Python.

## Features

- Pokédex with retro look and feel
- User accounts (Trainers)
- Team and storage boxes (to hold Pokémon)
- Favorites list
- Badges and Trainer cards
- Battle Pokémon (player vs player)
- Battle against CPU rivals
- Check weaknesses and type matchups
- Compare two Pokémon side by side
- Get battle advice
- Browse all Pokémon (by number or A-Z)
- Pokémon of the Day
- Route maps: see the wild Pokémon on any route (e.g. Route 104, Route 1) and open the full map in your browser
- Music: search for the original music, or play your own audio files
- Cache manager to see and clear saved data

## Requirements

- Python 3
- Tkinter
- Internet connection to retrieve Pokémon sprites and information (the first time; after that, things you already looked up are saved and work offline)

## Run from source

Clone the project:

```bash
git clone https://github.com/PIπ_KING/Turtle-Pokedex-Advance.git
```

Navigate into the project directory:

```bash
cd Turtle-Pokedex-Advance
```

Start the app:

```bash
python3 turtle_pokedex.py
```

## Route maps

Open **Route Maps** from the menu. Type a route such as `104`, `hoenn route 104` or `kanto route 1`, or browse by region. Each route lists the wild Pokémon, how you meet them, their levels and the games they appear in. **Open Map in Browser** opens the route's Bulbapedia page, which has the real map. Data comes from PokeAPI, which has no map images and is missing some newer games.

## Music

Open **Music** from the menu. You can search for a theme (for example Lavender Town) or pick a game and a theme such as Route or Trainer Battle. This opens a search in your browser. You can also put your own audio files (mp3, wav, ogg, m4a, flac, aac) in `~/.turtle_pokedex/music` and play them from **My Music Folder**. The Pokémon music is not included in this app.

## Downloads

Ready-to-use versions for Linux and Windows will be available via GitHub Releases.

## Data and privacy

All cached data and Trainer information is saved to your machine in `~/.turtle_pokedex`. Passwords are salted and hashed. Nothing is sent anywhere except requests to PokeAPI for Pokémon data and sprites.

Requests to PokeAPI are made one at a time, and every result is saved, so each Pokémon, move, sprite and route is only fetched once.

## Disclaimer

This is a fan-made application. Pokémon and other trademarks mentioned are the property of their owners. Using this app does not imply any affiliation or sponsorship by The Pokémon Company, Game Freak, or Nintendo.
