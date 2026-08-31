#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "AureliaCityBuilder.generated.h"

class UMaterialInterface;
class UStaticMesh;

/**
 * Spawns an original dusk coastal street grid from Engine primitives.
 * This is the playable stand-in until Epic City Sample is added for Nanite density.
 * Not GTA 6. Not Vice City. Not Leonida.
 */
UCLASS()
class AURELIADRIVE_API AAureliaCityBuilder : public AActor
{
	GENERATED_BODY()

public:
	AAureliaCityBuilder();

	void Build();

private:
	void AddBox(const FVector& Location, const FVector& Scale, const FLinearColor& Color, bool bCollision);
	void AddPalm(const FVector& Location);
	UMaterialInstanceDynamic* Paint(const FLinearColor& Color);

	UPROPERTY()
	TObjectPtr<UStaticMesh> CubeMesh;

	UPROPERTY()
	TObjectPtr<UStaticMesh> CylinderMesh;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> ShapeMaterial;

	UPROPERTY()
	TArray<TObjectPtr<UMaterialInstanceDynamic>> Paints;

	FRandomStream Rng;
};
