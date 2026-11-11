export type CanValue = number | { raw: number };

export type CanSignal = {
  name: string;
  minimum: number | null;
  maximum: number | null;
  scale: number;
  unit: string | null;
  comment: string | null;
  choices: { raw: number; label: string }[];
  multiplexer: string | null;
  multiplexer_ids: number[] | null;
};

export type CanMessage = {
  name: string;
  id: number;
  can_fd: boolean;
  extended: boolean;
  length: number;
  cycle_time: number;
  signals: CanSignal[];
};

export type CanCatalog = {
  path: string;
  key: string;
  messages: CanMessage[];
  message_count: number;
};
