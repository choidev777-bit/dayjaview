import { useEffect } from 'react';
import type { ProductRepository, RepositoryResource } from '../domain/contracts';
import { useAsyncResource } from './useAsyncResource';

export function useRepositoryResource<T>(
  repository: ProductRepository,
  resource: RepositoryResource,
  loader: () => Promise<T>,
  dependencies: readonly unknown[],
) {
  const result = useAsyncResource(loader, dependencies);

  useEffect(
    () => repository.subscribe(resource, result.refresh),
    [repository, resource, result.refresh],
  );

  return result;
}
