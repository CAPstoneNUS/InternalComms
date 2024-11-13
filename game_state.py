import os
import json
import threading


class VestState:
    def __init__(self):
        self._state = {
            "shield": 0,
            "health": 100,
        }
        self._lock = threading.Lock()
        self._pending_state = None

    def getState(self):
        with self._lock:
            return self._state.copy()

    def updateState(self, **kwargs):
        with self._lock:
            self._pending_state = self._state.copy()
            for key, value in kwargs.items():
                if key in self._pending_state:
                    self._pending_state[key] = value

    def applyState(self, **kwargs):
        with self._lock:
            if self._pending_state is None:
                print(f"No pending state to apply for {kwargs}")
                return False

            for key, value in kwargs.items():
                # check if health is less than 0 and reset the vest state
                if key == "health" and value <= 0:
                    print("Player dead. Refreshing health...")
                    self._pending_state = self._state.copy()
                    self._pending_state["health"] = 100
                    self._pending_state["shield"] = 0
                    return False
                if key not in self._pending_state or self._pending_state[key] != value:
                    print(
                        f"Invalid state: {key}={value}, expected {key}={self._pending_state[key]}"
                    )
                    return False

            self._state = self._pending_state
            self._pending_state = None
            print(f"Vest state successfully updated: {self._state}")
            return True

    def applyDamage(self, damage):
        with self._lock:
            self._pending_state = self._state.copy()
            if self._pending_state["shield"] >= damage:
                self._pending_state["shield"] -= damage
            else:
                remaining_damage = damage - self._pending_state["shield"]
                self._pending_state["shield"] = 0
                new_health = self._pending_state["health"] - remaining_damage
                if new_health <= 0:
                    self._pending_state["health"] = 100
                    self._pending_state["shield"] = 0
                else:
                    self._pending_state["health"] = new_health

    def refreshShield(self):
        with self._lock:
            self._pending_state = self._state.copy()
            self._pending_state["shield"] = 30


class GunState:
    def __init__(self):
        self._state = {
            "bullets": 6,
        }
        self._lock = threading.Lock()
        self._pending_state = None

    def getState(self):
        with self._lock:
            return self._state.copy()

    def updateState(self, **kwargs):
        with self._lock:
            self._pending_state = self._state.copy()
            for key, value in kwargs.items():
                if key in self._pending_state:
                    self._pending_state[key] = value

    def applyState(self, **kwargs):
        with self._lock:
            if self._pending_state is None:
                print(f"No pending state to apply for {kwargs}")
                return False

            for key, value in kwargs.items():
                if key not in self._pending_state or self._pending_state[key] != value:
                    print(
                        f"Invalid state: {key}={value}, expected {key}={self._pending_state[key]}"
                    )
                    return False

            self._state = self._pending_state
            self._pending_state = None
            print(f"Gun state successfully updated: {self._state}")
            return True

    def useBullet(self):
        with self._lock:
            if self._state["bullets"] > 0:
                self._pending_state = self._state.copy()
                self._pending_state["bullets"] -= 1
                return True
            print("No bullets left")
            return False

    def reload(self):
        with self._lock:
            self._pending_state = self._state.copy()
            self._pending_state["bullets"] = 6


class GameState:
    def __init__(self, config):
        self.vest_state = VestState()
        self.gun_state = GunState()
        self.config = config

        self.loadState()

    def loadState(self):
        try:
            if os.path.exists(f'{self.config["game"]["player_id"]}_game_state.json'):
                with open(f'{self.config["game"]["player_id"]}_game_state.json', 'r') as f:
                    print("Loading from game_state.json...")
                    saved_state = json.load(f)
                    
                    if 'shield' in saved_state or 'health' in saved_state:
                        vest_data = {
                            'shield': saved_state.get('shield', 0),
                            'health': saved_state.get('health', 100)
                        }
                        self.vest_state.updateState(**vest_data)
                        self.vest_state.applyState(**vest_data)
                    
                    if 'bullets' in saved_state:
                        gun_data = {'bullets': saved_state['bullets']}
                        self.gun_state.updateState(**gun_data)
                        self.gun_state.applyState(**gun_data)
                        
        except Exception as e:
            print(f"Error: {e}")
            print("Applying default game state...")

    def saveState(self):
        try:
            state = self.getState()
            with open(f'{self.config["game"]["player_id"]}_game_state.json', 'w') as f:
                json.dump(state, f)
            print(f'Game state saved to {self.config["game"]["player_id"]}_game_state.json')
        except Exception as e:
            print(f"Error saving game state: {e}")

    def getState(self):
        return {**self.vest_state.getState(), **self.gun_state.getState()}

    def updateVestState(self, **kwargs):
        self.vest_state.updateState(**kwargs)

    def applyVestState(self, **kwargs):
        return self.vest_state.applyState(**kwargs)

    def updateGunState(self, **kwargs):
        self.gun_state.updateState(**kwargs)

    def applyGunState(self, **kwargs):
        return self.gun_state.applyState(**kwargs)

    def applyDamage(self, damage):
        print(f"-{damage} damage")
        self.vest_state.applyDamage(damage)

    def useBullet(self):
        return self.gun_state.useBullet()

    def refreshShield(self):
        print("+30 shield")
        self.vest_state.refreshShield()

    def getRemainingBullets(self):
        return self.gun_state.getState()["bullets"]

    def getShieldHealth(self):
        state = self.vest_state.getState()
        return state["shield"], state["health"]
