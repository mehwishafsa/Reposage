/**
 * Talks to the backend server.
 */
export class Api {
  fetchUser(id: number) {
    return this.request(`/users/${id}`);
  }

  private request(url: string) {
    return fetch(url);
  }

  onError = () => {
    this.request("/errors");
  };
}

export interface User {
  id: number;
}
