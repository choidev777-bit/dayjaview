import { createContext, useContext, type ReactNode } from 'react';
import type { ProductRepository } from '../domain/contracts';

const RepositoryContext = createContext<ProductRepository | null>(null);

export function RepositoryProvider({
  repository,
  children,
}: {
  repository: ProductRepository;
  children: ReactNode;
}) {
  return <RepositoryContext.Provider value={repository}>{children}</RepositoryContext.Provider>;
}

export function useRepository(): ProductRepository {
  const repository = useContext(RepositoryContext);
  if (!repository) throw new Error('ProductRepository가 연결되지 않았습니다.');
  return repository;
}
