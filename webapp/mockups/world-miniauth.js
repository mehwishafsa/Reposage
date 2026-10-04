window.WORLD = {
 "format": 1,
 "seed": 100918802043319,
 "width": 38,
 "depth": 24,
 "regions": [
  {
   "id": "miniauth",
   "name": "miniauth/",
   "x": 2,
   "y": 1,
   "w": 34,
   "d": 21
  }
 ],
 "villages": [
  {
   "id": "miniauth/auth.py",
   "name": "auth.py",
   "x": 4,
   "y": 3,
   "w": 13,
   "d": 6,
   "summary": "Implements the authentication workflow: verifying passwords, generating session tokens, and processing login requests. It depends on db.py to fetch user records and verify stored password hashes.",
   "gate": [
    10,
    9
   ]
  },
  {
   "id": "miniauth/db.py",
   "name": "db.py",
   "x": 21,
   "y": 3,
   "w": 13,
   "d": 6,
   "summary": "Manages an in-memory user database stored in a dictionary. Provides functions to look up users, create or update records, and hash passwords. Used by auth.py and main.py to store and retrieve user information.",
   "gate": [
    27,
    9
   ]
  },
  {
   "id": "miniauth/main.py",
   "name": "main.py",
   "x": 4,
   "y": 13,
   "w": 13,
   "d": 6,
   "summary": "Routes HTTP-like requests to the appropriate handler functions. Handles login requests by calling the auth module and registration requests by creating new users in the database.",
   "gate": [
    10,
    19
   ]
  }
 ],
 "buildings": [
  {
   "id": "miniauth/auth.py::verify_password",
   "name": "verify_password",
   "kind": "function",
   "file": "miniauth/auth.py",
   "x": 5,
   "y": 5,
   "w": 2,
   "d": 2,
   "h": 2,
   "lines": 3,
   "start": 8,
   "end": 10,
   "door": [
    6,
    7
   ],
   "summary": "Checks whether a raw password matches its stored hash."
  },
  {
   "id": "miniauth/auth.py::generate_token",
   "name": "generate_token",
   "kind": "function",
   "file": "miniauth/auth.py",
   "x": 9,
   "y": 5,
   "w": 2,
   "d": 2,
   "h": 2,
   "lines": 4,
   "start": 13,
   "end": 16,
   "door": [
    10,
    7
   ],
   "summary": "Creates a session token that includes the username, role, and current timestamp."
  },
  {
   "id": "miniauth/auth.py::login",
   "name": "login",
   "kind": "function",
   "file": "miniauth/auth.py",
   "x": 13,
   "y": 5,
   "w": 2,
   "d": 2,
   "h": 6,
   "lines": 15,
   "start": 19,
   "end": 33,
   "door": [
    14,
    7
   ],
   "summary": "Authenticates a user by looking them up, verifying their password, and returning a session token on success."
  },
  {
   "id": "miniauth/db.py::get_user",
   "name": "get_user",
   "kind": "function",
   "file": "miniauth/db.py",
   "x": 22,
   "y": 5,
   "w": 2,
   "d": 2,
   "h": 2,
   "lines": 3,
   "start": 9,
   "end": 11,
   "door": [
    23,
    7
   ],
   "summary": "Looks up a user record by username, returning None if not found."
  },
  {
   "id": "miniauth/db.py::save_user",
   "name": "save_user",
   "kind": "function",
   "file": "miniauth/db.py",
   "x": 26,
   "y": 5,
   "w": 2,
   "d": 2,
   "h": 2,
   "lines": 4,
   "start": 14,
   "end": 17,
   "door": [
    27,
    7
   ],
   "summary": "Creates or updates a user record with a username, hashed password, and role."
  },
  {
   "id": "miniauth/db.py::hash_password",
   "name": "hash_password",
   "kind": "function",
   "file": "miniauth/db.py",
   "x": 30,
   "y": 5,
   "w": 2,
   "d": 2,
   "h": 2,
   "lines": 3,
   "start": 20,
   "end": 22,
   "door": [
    31,
    7
   ],
   "summary": "Converts a raw password string into a hashed form for safe storage."
  },
  {
   "id": "miniauth/main.py::handle_login_request",
   "name": "handle_login_request",
   "kind": "function",
   "file": "miniauth/main.py",
   "x": 5,
   "y": 15,
   "w": 2,
   "d": 2,
   "h": 3,
   "lines": 8,
   "start": 7,
   "end": 14,
   "door": [
    6,
    17
   ],
   "summary": "Processes a login request by extracting username and password, authenticating them, and returning a token if successful."
  },
  {
   "id": "miniauth/main.py::handle_register_request",
   "name": "handle_register_request",
   "kind": "function",
   "file": "miniauth/main.py",
   "x": 9,
   "y": 15,
   "w": 2,
   "d": 2,
   "h": 3,
   "lines": 6,
   "start": 17,
   "end": 22,
   "door": [
    10,
    17
   ],
   "summary": "Processes a registration request by hashing the password and saving a new user to the database."
  },
  {
   "id": "miniauth/main.py::route",
   "name": "route",
   "kind": "function",
   "file": "miniauth/main.py",
   "x": 13,
   "y": 15,
   "w": 2,
   "d": 2,
   "h": 3,
   "lines": 7,
   "start": 25,
   "end": 31,
   "door": [
    14,
    17
   ],
   "summary": "Dispatches incoming requests to the correct handler based on the request path."
  }
 ],
 "roads": [
  {
   "kind": "call",
   "from": "miniauth/auth.py::login",
   "to": "miniauth/auth.py::generate_token",
   "cells": [
    [
     14,
     7
    ],
    [
     13,
     7
    ],
    [
     12,
     7
    ],
    [
     11,
     7
    ],
    [
     10,
     7
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/auth.py::login",
   "to": "miniauth/auth.py::verify_password",
   "cells": [
    [
     14,
     7
    ],
    [
     13,
     7
    ],
    [
     12,
     7
    ],
    [
     11,
     7
    ],
    [
     10,
     7
    ],
    [
     9,
     7
    ],
    [
     8,
     7
    ],
    [
     7,
     7
    ],
    [
     6,
     7
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/auth.py::login",
   "to": "miniauth/db.py::get_user",
   "cells": [
    [
     14,
     7
    ],
    [
     15,
     7
    ],
    [
     16,
     7
    ],
    [
     17,
     7
    ],
    [
     18,
     7
    ],
    [
     19,
     7
    ],
    [
     20,
     7
    ],
    [
     21,
     7
    ],
    [
     22,
     7
    ],
    [
     23,
     7
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/auth.py::verify_password",
   "to": "miniauth/db.py::hash_password",
   "cells": [
    [
     6,
     7
    ],
    [
     7,
     7
    ],
    [
     8,
     7
    ],
    [
     9,
     7
    ],
    [
     10,
     7
    ],
    [
     11,
     7
    ],
    [
     12,
     7
    ],
    [
     13,
     7
    ],
    [
     14,
     7
    ],
    [
     15,
     7
    ],
    [
     16,
     7
    ],
    [
     17,
     7
    ],
    [
     18,
     7
    ],
    [
     19,
     7
    ],
    [
     20,
     7
    ],
    [
     21,
     7
    ],
    [
     22,
     7
    ],
    [
     23,
     7
    ],
    [
     24,
     7
    ],
    [
     25,
     7
    ],
    [
     26,
     7
    ],
    [
     27,
     7
    ],
    [
     28,
     7
    ],
    [
     29,
     7
    ],
    [
     30,
     7
    ],
    [
     31,
     7
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/main.py::handle_login_request",
   "to": "miniauth/auth.py::login",
   "cells": [
    [
     6,
     17
    ],
    [
     7,
     17
    ],
    [
     7,
     16
    ],
    [
     7,
     15
    ],
    [
     7,
     14
    ],
    [
     7,
     13
    ],
    [
     7,
     12
    ],
    [
     7,
     11
    ],
    [
     7,
     10
    ],
    [
     7,
     9
    ],
    [
     7,
     8
    ],
    [
     7,
     7
    ],
    [
     8,
     7
    ],
    [
     9,
     7
    ],
    [
     10,
     7
    ],
    [
     11,
     7
    ],
    [
     12,
     7
    ],
    [
     13,
     7
    ],
    [
     14,
     7
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/main.py::handle_register_request",
   "to": "miniauth/db.py::hash_password",
   "cells": [
    [
     10,
     17
    ],
    [
     9,
     17
    ],
    [
     8,
     17
    ],
    [
     7,
     17
    ],
    [
     7,
     16
    ],
    [
     7,
     15
    ],
    [
     7,
     14
    ],
    [
     7,
     13
    ],
    [
     7,
     12
    ],
    [
     7,
     11
    ],
    [
     7,
     10
    ],
    [
     7,
     9
    ],
    [
     7,
     8
    ],
    [
     7,
     7
    ],
    [
     8,
     7
    ],
    [
     9,
     7
    ],
    [
     10,
     7
    ],
    [
     11,
     7
    ],
    [
     12,
     7
    ],
    [
     13,
     7
    ],
    [
     14,
     7
    ],
    [
     15,
     7
    ],
    [
     16,
     7
    ],
    [
     17,
     7
    ],
    [
     18,
     7
    ],
    [
     19,
     7
    ],
    [
     20,
     7
    ],
    [
     21,
     7
    ],
    [
     22,
     7
    ],
    [
     23,
     7
    ],
    [
     24,
     7
    ],
    [
     25,
     7
    ],
    [
     26,
     7
    ],
    [
     27,
     7
    ],
    [
     28,
     7
    ],
    [
     29,
     7
    ],
    [
     30,
     7
    ],
    [
     31,
     7
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/main.py::handle_register_request",
   "to": "miniauth/db.py::save_user",
   "cells": [
    [
     10,
     17
    ],
    [
     9,
     17
    ],
    [
     8,
     17
    ],
    [
     7,
     17
    ],
    [
     7,
     16
    ],
    [
     7,
     15
    ],
    [
     7,
     14
    ],
    [
     7,
     13
    ],
    [
     7,
     12
    ],
    [
     7,
     11
    ],
    [
     7,
     10
    ],
    [
     7,
     9
    ],
    [
     7,
     8
    ],
    [
     7,
     7
    ],
    [
     8,
     7
    ],
    [
     9,
     7
    ],
    [
     10,
     7
    ],
    [
     11,
     7
    ],
    [
     12,
     7
    ],
    [
     13,
     7
    ],
    [
     14,
     7
    ],
    [
     15,
     7
    ],
    [
     16,
     7
    ],
    [
     17,
     7
    ],
    [
     18,
     7
    ],
    [
     19,
     7
    ],
    [
     20,
     7
    ],
    [
     21,
     7
    ],
    [
     22,
     7
    ],
    [
     23,
     7
    ],
    [
     24,
     7
    ],
    [
     25,
     7
    ],
    [
     26,
     7
    ],
    [
     27,
     7
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/main.py::route",
   "to": "miniauth/main.py::handle_login_request",
   "cells": [
    [
     14,
     17
    ],
    [
     13,
     17
    ],
    [
     12,
     17
    ],
    [
     11,
     17
    ],
    [
     10,
     17
    ],
    [
     9,
     17
    ],
    [
     8,
     17
    ],
    [
     7,
     17
    ],
    [
     6,
     17
    ]
   ],
   "guessed": false
  },
  {
   "kind": "call",
   "from": "miniauth/main.py::route",
   "to": "miniauth/main.py::handle_register_request",
   "cells": [
    [
     14,
     17
    ],
    [
     13,
     17
    ],
    [
     12,
     17
    ],
    [
     11,
     17
    ],
    [
     10,
     17
    ]
   ],
   "guessed": false
  },
  {
   "kind": "import",
   "from": "miniauth/auth.py",
   "to": "miniauth/db.py",
   "cells": [
    [
     10,
     9
    ],
    [
     10,
     8
    ],
    [
     10,
     7
    ],
    [
     11,
     7
    ],
    [
     12,
     7
    ],
    [
     13,
     7
    ],
    [
     14,
     7
    ],
    [
     15,
     7
    ],
    [
     16,
     7
    ],
    [
     17,
     7
    ],
    [
     18,
     7
    ],
    [
     19,
     7
    ],
    [
     20,
     7
    ],
    [
     21,
     7
    ],
    [
     22,
     7
    ],
    [
     23,
     7
    ],
    [
     24,
     7
    ],
    [
     25,
     7
    ],
    [
     26,
     7
    ],
    [
     27,
     7
    ],
    [
     27,
     8
    ],
    [
     27,
     9
    ]
   ],
   "guessed": false
  },
  {
   "kind": "import",
   "from": "miniauth/main.py",
   "to": "miniauth/auth.py",
   "cells": [
    [
     10,
     19
    ],
    [
     10,
     18
    ],
    [
     10,
     17
    ],
    [
     9,
     17
    ],
    [
     8,
     17
    ],
    [
     7,
     17
    ],
    [
     7,
     16
    ],
    [
     7,
     15
    ],
    [
     7,
     14
    ],
    [
     7,
     13
    ],
    [
     7,
     12
    ],
    [
     7,
     11
    ],
    [
     7,
     10
    ],
    [
     7,
     9
    ],
    [
     8,
     9
    ],
    [
     9,
     9
    ],
    [
     10,
     9
    ]
   ],
   "guessed": false
  },
  {
   "kind": "import",
   "from": "miniauth/main.py",
   "to": "miniauth/db.py",
   "cells": [
    [
     10,
     19
    ],
    [
     10,
     18
    ],
    [
     10,
     17
    ],
    [
     9,
     17
    ],
    [
     8,
     17
    ],
    [
     7,
     17
    ],
    [
     7,
     16
    ],
    [
     7,
     15
    ],
    [
     7,
     14
    ],
    [
     7,
     13
    ],
    [
     7,
     12
    ],
    [
     7,
     11
    ],
    [
     7,
     10
    ],
    [
     7,
     9
    ],
    [
     8,
     9
    ],
    [
     9,
     9
    ],
    [
     10,
     9
    ],
    [
     10,
     8
    ],
    [
     10,
     7
    ],
    [
     11,
     7
    ],
    [
     12,
     7
    ],
    [
     13,
     7
    ],
    [
     14,
     7
    ],
    [
     15,
     7
    ],
    [
     16,
     7
    ],
    [
     17,
     7
    ],
    [
     18,
     7
    ],
    [
     19,
     7
    ],
    [
     20,
     7
    ],
    [
     21,
     7
    ],
    [
     22,
     7
    ],
    [
     23,
     7
    ],
    [
     24,
     7
    ],
    [
     25,
     7
    ],
    [
     26,
     7
    ],
    [
     27,
     7
    ],
    [
     27,
     8
    ],
    [
     27,
     9
    ]
   ],
   "guessed": false
  }
 ],
 "decor": [
  {
   "x": 2,
   "y": 0,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 3,
   "y": 0,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 14,
   "y": 0,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 15,
   "y": 0,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 16,
   "y": 0,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 26,
   "y": 0,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 28,
   "y": 0,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 1,
   "y": 1,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 2,
   "y": 1,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 4,
   "y": 1,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 5,
   "y": 1,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 11,
   "y": 1,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 14,
   "y": 1,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 15,
   "y": 1,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 17,
   "y": 1,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 36,
   "y": 1,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 1,
   "y": 2,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 6,
   "y": 2,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 15,
   "y": 2,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 21,
   "y": 2,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 29,
   "y": 2,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 1,
   "y": 3,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 36,
   "y": 3,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 18,
   "y": 4,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 19,
   "y": 4,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 37,
   "y": 4,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 0,
   "y": 5,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 1,
   "y": 5,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 36,
   "y": 5,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 0,
   "y": 6,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 1,
   "y": 6,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 37,
   "y": 6,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 0,
   "y": 7,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 37,
   "y": 7,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 0,
   "y": 8,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 3,
   "y": 8,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 19,
   "y": 8,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 20,
   "y": 8,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 35,
   "y": 8,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 37,
   "y": 8,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 19,
   "y": 9,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 33,
   "y": 9,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 1,
   "y": 10,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 12,
   "y": 10,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 26,
   "y": 10,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 30,
   "y": 10,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 35,
   "y": 10,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 36,
   "y": 10,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 37,
   "y": 10,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 0,
   "y": 11,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 1,
   "y": 11,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 4,
   "y": 11,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 9,
   "y": 11,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 11,
   "y": 11,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 12,
   "y": 11,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 13,
   "y": 11,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 19,
   "y": 11,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 27,
   "y": 11,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 35,
   "y": 11,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 36,
   "y": 11,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 37,
   "y": 11,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 12,
   "y": 12,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 15,
   "y": 12,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 18,
   "y": 12,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 27,
   "y": 12,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 28,
   "y": 12,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 37,
   "y": 12,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 0,
   "y": 13,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 1,
   "y": 13,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 18,
   "y": 13,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 27,
   "y": 13,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 36,
   "y": 13,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 37,
   "y": 13,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 1,
   "y": 14,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 18,
   "y": 14,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 25,
   "y": 14,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 26,
   "y": 14,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 36,
   "y": 14,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 37,
   "y": 14,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 25,
   "y": 15,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 28,
   "y": 15,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 30,
   "y": 15,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 31,
   "y": 15,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 32,
   "y": 15,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 34,
   "y": 15,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 26,
   "y": 16,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 36,
   "y": 16,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 3,
   "y": 17,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 25,
   "y": 17,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 26,
   "y": 17,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 34,
   "y": 17,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 37,
   "y": 17,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 0,
   "y": 18,
   "kind": "flower",
   "v": 1
  },
  {
   "x": 1,
   "y": 18,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 18,
   "y": 18,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 33,
   "y": 18,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 37,
   "y": 18,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 0,
   "y": 19,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 1,
   "y": 19,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 4,
   "y": 19,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 26,
   "y": 19,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 35,
   "y": 19,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 28,
   "y": 20,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 1,
   "y": 21,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 8,
   "y": 21,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 10,
   "y": 21,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 18,
   "y": 21,
   "kind": "flower",
   "v": 0
  },
  {
   "x": 32,
   "y": 21,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 33,
   "y": 21,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 37,
   "y": 21,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 3,
   "y": 23,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 5,
   "y": 23,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 10,
   "y": 23,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 11,
   "y": 23,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 12,
   "y": 23,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 17,
   "y": 23,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 18,
   "y": 23,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 20,
   "y": 23,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 21,
   "y": 23,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 22,
   "y": 23,
   "kind": "tree",
   "v": 0
  },
  {
   "x": 26,
   "y": 23,
   "kind": "tree",
   "v": 1
  },
  {
   "x": 27,
   "y": 23,
   "kind": "flower",
   "v": 2
  },
  {
   "x": 31,
   "y": 23,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 36,
   "y": 23,
   "kind": "tree",
   "v": 2
  },
  {
   "x": 37,
   "y": 23,
   "kind": "flower",
   "v": 2
  }
 ]
}
;
