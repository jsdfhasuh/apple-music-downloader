import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials

scope = "user-library-read"
SPOTIPY_CLIENT_ID = 'your-spotify-client-id'
SPOTIPY_CLIENT_SECRET = 'your-spotify-client-secret'
SPOTIPY_REDIRECT_URI = 'http://127.0.0.1:766/callback'
auth_manager = SpotifyClientCredentials(
    client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET
)
sp = spotipy.Spotify(auth_manager=auth_manager)


def get_artist_name(input_string):
    retry_count = 0
    while retry_count < 3:
        results = sp.search(q=input_string, type='artist', limit=10, market='CN')
        if results:
            # check all artists in the results
            for artist in results['artists']['items']:
                temp_name = artist['name'].strip()
                if temp_name.lower() == input_string.lower():
                    return temp_name
            if results['artists']['items']:
                artist_name = results['artists']['items'][0]['name']
                return artist_name.strip()
        if retry_count == 2:
            print("Failed to get artist name after 3 attempts.")
            return input_string
        retry_count += 1


if __name__ == "__main__":

    input_string = "Alex Connors"
    artist_name = get_artist_name(input_string)
    if artist_name:
        print(f"Artist name: {artist_name}")
    else:
        print("No artist found.")
