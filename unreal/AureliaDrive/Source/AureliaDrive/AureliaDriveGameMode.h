#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "AureliaDriveGameMode.generated.h"

/**
 * Dusk coastal street race. Unreal Engine 5 coding source.
 * Lumen / Nanite / virtual shadows = GTA 6 *class*. Original city = not GTA 6 files.
 */
UCLASS()
class AURELIADRIVE_API AAureliaDriveGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	AAureliaDriveGameMode();

	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	UFUNCTION(BlueprintPure, Category = "Aurelia")
	FString GetLapText() const;

	UFUNCTION(BlueprintPure, Category = "Aurelia")
	FString GetStatusText() const;

	UFUNCTION(BlueprintPure, Category = "Aurelia")
	bool IsRaceFinished() const { return bFinished; }

private:
	void SpawnDusk();
	void SpawnCity();
	void SpawnGates();
	void PlaceVehicle();
	void AdvanceGate();

	TArray<FVector> Gates;

	int32 NextGate = 0;
	int32 Lap = 1;
	bool bFinished = false;
};
